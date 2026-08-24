# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import os
import base64
import hashlib
import re
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent

from .constants import DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT
from .helpers import _flat_get, _missing_optional_model_dependency, _now_ts, _path_text, _photo_group_request_matches, _safe_float, _safe_int, _set_into_config, _single_line, _today_key
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS
from .photo_reference_catalog import (
    CATALOG_VERSION,
    CatalogValidationError,
    PhotoReference,
    add_reference,
    delete_reference,
    load_catalog,
    validate_and_serialize,
)
from .photo_prompt_context import PhotoPromptSection
from .persona_config import runtime_persona_setting


_PHOTO_REFERENCE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


class CommandHandlersMixin:
    """Implementation bodies for command handlers registered in main.py."""

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

    def _companion_manual_clean_multiline(self, value: Any, limit: int = 1800) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
        text = re.sub(r"```(?:json|text|markdown)?\s*", "", text, flags=re.I)
        text = text.replace("```", "")
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text[:limit].strip()

    def _companion_manual_clean_question_text(self, value: Any, limit: int = 260) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        text = re.sub(r"\[CQ:image,[^\]]+\]", " ", text, flags=re.I)
        text = re.sub(r"\[(?:图片|image|Image|IMAGE)\]", " ", text)
        text = re.sub(r"【(?:图片|image)】", " ", text, flags=re.I)
        text = re.sub(r"\s+", " ", text)
        return _single_line(text, limit)

    def _companion_manual_current_group_note(self, event: AstrMessageEvent | None = None) -> str:
        group_id = ""
        if event is not None:
            try:
                group_id = self._extract_group_id_from_event(event)
            except Exception:
                group_id = ""
        if not group_id:
            return ""
        allowed = False
        try:
            allowed = bool(self._group_enabled_for_event(group_id))
        except Exception:
            allowed = False
        mode = _single_line(runtime_persona_setting(self, 'group_access_mode', ""), 20) or "unknown"
        return f"当前群：{group_id}｜群聊陪伴：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_companion', False))}｜名单模式：{mode}｜本群可用：{self._feature_on_text(allowed)}"

    def _companion_manual_setting_snapshot(self) -> list[str]:
        rest_probability = _safe_float(runtime_persona_setting(self, 'rest_reply_probability', 0.0), 0.0, 0.0)
        if rest_probability <= 1:
            rest_probability_text = f"{rest_probability * 100:.0f}%"
        else:
            rest_probability_text = f"{rest_probability:.0f}%"
        silence_confidence = _safe_float(runtime_persona_setting(self, 'smart_silence_min_confidence', 0.0), 0.0, 0.0)
        if silence_confidence <= 1:
            silence_confidence_text = f"{silence_confidence * 100:.0f}%"
        else:
            silence_confidence_text = f"{silence_confidence:.0f}%"
        reply_style = str(runtime_persona_setting(self, 'reply_style_prompt', "") or "").strip()
        command_photo_limit = self._command_photo_generation_daily_limit()
        if command_photo_limit < 0:
            command_photo_limit_text = "不限量（-1）"
        elif command_photo_limit == 0:
            command_photo_limit_text = "不允许（0）"
        else:
            command_photo_limit_text = f"{command_photo_limit} 次"
        return [
            f"群聊连续对话：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_conversation_followup', False))}，窗口 {runtime_persona_setting(self, 'group_conversation_followup_seconds', 0)} 秒，最多 {runtime_persona_setting(self, 'group_conversation_followup_max_turns', 0)} 轮",
            f"高强度收口：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_high_intensity_mode', False))}，{runtime_persona_setting(self, 'group_high_intensity_wakeup_window_seconds', 0)} 秒内 {runtime_persona_setting(self, 'group_high_intensity_wakeup_threshold', 0)} 次唤醒后持续 {runtime_persona_setting(self, 'group_high_intensity_cooldown_seconds', 0)} 秒",
            f"高强度合并：等待 {runtime_persona_setting(self, 'group_high_intensity_merge_seconds', 0)} 秒，范围 {runtime_persona_setting(self, 'group_high_intensity_merge_scope', 'group')}，最多 {runtime_persona_setting(self, 'group_high_intensity_max_merge_messages', 0)} 条",
            f"消息收口：{self._feature_on_text(runtime_persona_setting(self, 'enable_message_debounce', False))}，智能文本收口 {self._feature_on_text(runtime_persona_setting(self, 'enable_smart_message_debounce', False))}，文本最长等待 {runtime_persona_setting(self, 'text_message_debounce_max_wait_seconds', 0)} 秒",
            f"群聊唤醒增强：{self._feature_on_text(runtime_persona_setting(self, 'enable_group_wakeup_enhancement', False))}，全局强唤醒词 {len(runtime_persona_setting(self, 'group_wakeup_direct_words', []) or [])} 个，主要用户专属强唤醒词 {len(runtime_persona_setting(self, 'group_wakeup_owner_direct_words', []) or [])} 个，短唤醒补话等待 {runtime_persona_setting(self, 'group_wakeup_short_text_wait_seconds', 0)} 秒",
            f"休息回复闸门：{self._feature_on_text(runtime_persona_setting(self, 'enable_rest_reply_simulation', False))}，模式 {runtime_persona_setting(self, 'rest_reply_mode', 'probability')}，概率 {rest_probability_text}，模型阈值 {runtime_persona_setting(self, 'rest_reply_llm_threshold', 0)}，清醒宽限 {runtime_persona_setting(self, 'rest_reply_awake_grace_minutes', 0)} 分钟",
            f"繁忙回复闸门：{self._feature_on_text(runtime_persona_setting(self, 'enable_busy_reply_gate', False))}，私聊延迟 {runtime_persona_setting(self, 'busy_reply_min_delay_seconds', 60)}-{runtime_persona_setting(self, 'busy_reply_max_delay_seconds', 300)} 秒，群聊上限 12 秒，忙完后主动缓冲 {runtime_persona_setting(self, 'busy_reply_proactive_resume_buffer_minutes', 10)} 分钟",
            f"智能沉默：{self._feature_on_text(runtime_persona_setting(self, 'enable_smart_silence', True))}，模式 {runtime_persona_setting(self, 'smart_silence_judge_mode', 'boundary_only')}，置信度 {silence_confidence_text}，超时 {runtime_persona_setting(self, 'smart_silence_model_timeout_seconds', 0)} 秒",
            f"被动回复复核：{self._feature_on_text(runtime_persona_setting(self, 'enable_passive_response_review', runtime_persona_setting(self, 'enable_response_self_review', True)))}，模式 {runtime_persona_setting(self, 'passive_review_mode', runtime_persona_setting(self, 'response_review_mode', 'severe_only'))}，强度 {runtime_persona_setting(self, 'passive_review_strength', 'lenient')}，长度阈值 {runtime_persona_setting(self, 'response_review_max_chars', 260)} 字；框架异常文本外发拦截：{self._feature_on_text(runtime_persona_setting(self, 'enable_framework_error_leak_guard', True))}",
            f"主动消息终审：{self._feature_on_text(runtime_persona_setting(self, 'enable_proactive_message_review', True))}，模式 {runtime_persona_setting(self, 'proactive_review_mode', 'full')}，强度 {runtime_persona_setting(self, 'proactive_review_strength', 'lenient')}",
            f"用户请求生图每日上限：{command_photo_limit_text}；非指令生图：{_single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', 'tool_first'), 24) or 'tool_first'}，规则快判{self._feature_on_text(runtime_persona_setting(self, 'enable_natural_language_photo_generation', False))}，每日上限 {runtime_persona_setting(self, 'natural_language_photo_generation_max_daily', 0)}",
            f"拟人状态：健康 {self._feature_on_text(runtime_persona_setting(self, 'enable_health_state', True))}，饥饿 {self._feature_on_text(runtime_persona_setting(self, 'enable_hunger_state', True))}，生理期 {self._feature_on_text(runtime_persona_setting(self, 'enable_cycle_state', True))}，强度 {runtime_persona_setting(self, 'humanized_state_intensity', 0)}",
            self._cycle_status_text(),
            f"回复风格：{'已配置' if reply_style else '未配置'}，长度 {len(reply_style)} 字",
        ]

    def _cycle_status_text(self) -> str:
        """Build the six-phase cycle position line for the status overview."""
        if not self._advanced_cycle_enabled():
            return "六阶段周期：未开启"
        try:
            runtime = self._advanced_cycle_runtime()
        except Exception:
            runtime = {}
        if not runtime or not runtime.get("phase_name"):
            return "六阶段周期：已开启（尚未进入首个周期）"
        parts = [
            f"{runtime.get('phase_name')} 第{runtime.get('day_in_phase', 1)}/{runtime.get('phase_days', 1)}天",
            f"周期第{runtime.get('cycle_day', 0)}/{runtime.get('cycle_days', 0)}天",
        ]
        if runtime.get("next_phase_name"):
            parts.append(f"下一阶段{runtime.get('next_phase_name')}")
        try:
            discomfort = self._active_cycle_discomfort_conditions()
        except Exception:
            discomfort = []
        if isinstance(discomfort, list) and discomfort:
            labels = "、".join(
                _single_line(item.get("label") or item.get("type"), 40)
                for item in discomfort[:3]
                if isinstance(item, dict)
            )
            if labels:
                parts.append(f"当前不适：{labels}")
        return "六阶段周期：" + "，".join(parts)
    def _companion_manual_runtime_snapshot(self, event: AstrMessageEvent | None = None) -> str:
        lines: list[str] = []
        group_id = ""
        sender_id = ""
        if event is not None:
            try:
                group_id = self._extract_group_id_from_event(event)
            except Exception:
                group_id = ""
            try:
                sender_id = str(event.get_sender_id())
            except Exception:
                sender_id = ""
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        try:
            sleep_delay = self._sleep_delay_override_state(clear_expired=True)
        except Exception:
            sleep_delay = {}
        if isinstance(sleep_delay, dict) and sleep_delay:
            until_text = _single_line(sleep_delay.get("until_text"), 24) or "-"
            lines.append(
                "临时晚睡覆盖："
                f"到 {until_text}"
                f"｜来源={_single_line(sleep_delay.get('user_text'), 60) or '用户今晚陪聊约定'}"
                "｜过期后自动恢复日程休息"
            )
        if group_id:
            groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
            group = groups.get(group_id) if isinstance(groups, dict) else None
            if isinstance(group, dict):
                try:
                    intensity = self._group_high_intensity_state(group, mutate=False)
                except Exception:
                    intensity = {}
                if isinstance(intensity, dict):
                    lines.append(
                        "当前群高强度："
                        f"{self._feature_on_text(intensity.get('active'))}"
                        f"｜原因={_single_line(intensity.get('reason'), 40) or '-'}"
                        f"｜近窗唤醒={_safe_int(intensity.get('recent_wakeups'), 0)}"
                        f"/{_safe_int(intensity.get('threshold'), 0)}"
                        f"｜剩余={_safe_float(intensity.get('remaining_seconds'), 0):.1f}s"
                    )
                active = group.get("active_bot_conversation") if isinstance(group.get("active_bot_conversation"), dict) else {}
                if active:
                    lines.append(
                        "当前群连续对话锚点："
                        f"sender={_single_line(active.get('sender_id'), 40) or '-'}"
                        f"｜turns={_safe_int(active.get('contextual_followups'), 0)}"
                        f"｜expires_in={max(0.0, _safe_float(active.get('expires_at'), 0) - _now_ts()):.1f}s"
                        f"｜last={_single_line(active.get('last_text'), 80) or '-'}"
                    )
                last_wakeup = group.get("last_group_wakeup") if isinstance(group.get("last_group_wakeup"), dict) else {}
                if last_wakeup:
                    lines.append(
                        "最近群唤醒："
                        f"{_single_line(last_wakeup.get('type'), 30) or '-'}"
                        f"｜{_single_line(last_wakeup.get('reason_label'), 60) or _single_line(last_wakeup.get('reason'), 60) or '-'}"
                        f"｜sender={_single_line(last_wakeup.get('sender_id'), 40) or '-'}"
                        f"｜text={_single_line(last_wakeup.get('text'), 80) or '-'}"
                    )
                recent = group.get("recent_messages") if isinstance(group.get("recent_messages"), list) else []
                recent_lines = []
                for item in recent[-5:]:
                    if not isinstance(item, dict):
                        continue
                    who = _single_line(item.get("identity_name") or item.get("name") or item.get("sender_id"), 24)
                    msg = _single_line(item.get("text"), 80)
                    if msg:
                        recent_lines.append(f"{who or '?'}: {msg}")
                if recent_lines:
                    lines.append("最近群消息：" + " / ".join(recent_lines))
        users = data.get("users") if isinstance(data.get("users"), dict) else {}
        user = users.get(sender_id) if sender_id and isinstance(users, dict) else None
        if isinstance(user, dict):
            lines.append(
                "当前用户状态："
                f"enabled={self._feature_on_text(user.get('enabled', True))}"
                f"｜role={_single_line(user.get('role'), 30) or '-'}"
                f"｜ignored={_safe_int(user.get('ignored_streak'), 0)}"
                f"｜last_seen={self._format_timestamp_elapsed(user.get('last_seen')) if callable(getattr(self, '_format_timestamp_elapsed', None)) else _single_line(user.get('last_seen'), 30)}"
            )
        debounce = data.get("smart_message_debounce") if isinstance(data.get("smart_message_debounce"), dict) else {}
        logs = debounce.get("recent_logs") if isinstance(debounce.get("recent_logs"), list) else []
        if logs:
            compact_logs = []
            for item in logs[-4:]:
                if not isinstance(item, dict):
                    continue
                compact_logs.append(
                    f"{_single_line(item.get('chat'), 10) or '-'}:{_single_line(item.get('decision'), 20) or '-'}"
                    f"/{_single_line(item.get('outcome'), 24) or '-'}"
                    f"({_single_line(item.get('reason'), 40) or '-'})"
                )
            if compact_logs:
                lines.append("最近智能收口：" + " / ".join(compact_logs))
        passive = data.get("passive_no_reply_records") if isinstance(data.get("passive_no_reply_records"), dict) else {}
        if passive:
            reasons = []
            passive_items = passive.get("items") if isinstance(passive.get("items"), list) else []
            for item in passive_items[:5]:
                if isinstance(item, dict):
                    reasons.append(f"{_single_line(item.get('reason'), 40)}×{_safe_int(item.get('count'), 1)}")
            if reasons:
                lines.append("最近被动未回复：" + " / ".join(reasons))
        if isinstance(user, dict):
            backlog = user.get("rest_reply_backlog")
            if isinstance(backlog, list) and backlog:
                rest_items = []
                for item in backlog[-3:]:
                    if not isinstance(item, dict):
                        continue
                    text = _single_line(item.get("text"), 50) or "非文本消息"
                    reason = _single_line(item.get("reason"), 32) or "-"
                    rest_items.append(f"{text}({reason})")
                if rest_items:
                    lines.append("休息待补看私聊：" + " / ".join(rest_items))
        tests = data.get("troubleshooting_test_results") if isinstance(data.get("troubleshooting_test_results"), dict) else {}
        if tests:
            test_lines = []
            for key, item in list(tests.items())[-4:]:
                if not isinstance(item, dict):
                    continue
                title = _single_line(item.get("title") or key, 24)
                if bool(item.get("pending")):
                    status = "pending"
                else:
                    status = "ok" if bool(item.get("ok")) else "fail"
                detail = _single_line(item.get("error") or item.get("detail"), 48)
                test_lines.append(f"{title}:{status}{('/' + detail) if detail else ''}")
            if test_lines:
                lines.append("最近排障测试：" + " / ".join(test_lines))
        review_summary_getter = getattr(self, "_proactive_review_audit_summary", None)
        if callable(review_summary_getter):
            try:
                review_summary = review_summary_getter(window_days=7)
            except Exception:
                review_summary = {}
            if isinstance(review_summary, dict) and review_summary.get("total", 0):
                counts = review_summary.get("decision_counts") if isinstance(review_summary.get("decision_counts"), dict) else {}
                count_text = "/".join(
                    f"{_single_line(key, 16)}={_safe_int(value, 0)}"
                    for key, value in sorted(counts.items())
                )
                lines.append(f"近7天主动复核：{count_text or '-'}")
                outcomes = review_summary.get("reply_outcomes") if isinstance(review_summary.get("reply_outcomes"), dict) else {}
                if outcomes:
                    reply_rate = review_summary.get("reply_rate_24h")
                    rate_text = f"{reply_rate}%" if isinstance(reply_rate, (int, float)) else "待积累"
                    lines.append(
                        "主动回应（需回应路线）："
                        f"24h内回应={_safe_int(outcomes.get('replied_24h'), 0)}/"
                        f"未回应={_safe_int(outcomes.get('no_reply_24h'), 0)}/"
                        f"待观察={_safe_int(outcomes.get('pending'), 0)}，回应率={rate_text}"
                    )
                fallback_count = _safe_int(review_summary.get("consecutive_fallback_releases"), 0)
                if fallback_count >= 10:
                    lines.append(f"主动复核告警：模型已连续放行 {fallback_count} 条原文，请检查 RESPONSE_REVIEW_PROVIDER_ID")
        recent_photos = data.get("recent_photo_generations") if isinstance(data.get("recent_photo_generations"), list) else []
        photo_lines = []
        for item in recent_photos[:2]:
            if not isinstance(item, dict):
                continue
            kind = _single_line(item.get("kind"), 24) or "-"
            backend = _single_line(item.get("backend"), 36) or "-"
            status = "ok" if bool(item.get("ok")) else "fail"
            note = _single_line(item.get("note"), 48)
            reference = "ref" if bool(item.get("reference")) else "no-ref"
            prompt = _single_line(item.get("prompt"), 60)
            photo_lines.append(f"{kind}/{backend}/{status}/{reference}{('/' + note) if note else ''}{('｜' + prompt) if prompt else ''}")
        if photo_lines:
            lines.append("最近生图：" + " / ".join(photo_lines))
        return "\n".join(lines)

    def _companion_manual_config_specs(self) -> dict[str, dict[str, Any]]:
        return {
            "enable_group_companion": {"type": "bool", "label": "群聊陪伴总开关"},
            "enable_group_conversation_followup": {"type": "bool", "label": "群聊连续对话保持"},
            "group_conversation_followup_seconds": {"type": "int", "min": 0, "max": 600, "label": "群聊续接窗口秒数"},
            "group_conversation_followup_max_turns": {"type": "int", "min": 0, "max": 10, "label": "群聊连续续接上限"},
            "enable_group_high_intensity_mode": {"type": "bool", "label": "群聊高强度收口"},
            "group_high_intensity_wakeup_window_seconds": {"type": "int", "min": 15, "max": 600, "label": "高强度统计窗口秒数"},
            "group_high_intensity_wakeup_threshold": {"type": "int", "min": 2, "max": 20, "label": "高强度唤醒阈值"},
            "group_high_intensity_cooldown_seconds": {"type": "int", "min": 30, "max": 1800, "label": "高强度收口持续秒数"},
            "group_high_intensity_merge_seconds": {"type": "int", "min": 1, "max": 30, "label": "高强度合并等待秒数"},
            "group_high_intensity_max_merge_messages": {"type": "int", "min": 0, "max": 50, "label": "高强度最大合并消息数"},
            "group_high_intensity_merge_scope": {
                "type": "select",
                "choices": {"group", "same_user"},
                "aliases": {
                    "sender": "same_user",
                    "same_sender": "same_user",
                    "user": "same_user",
                    "同一用户": "same_user",
                    "同一发送者": "same_user",
                    "全群": "group",
                },
                "label": "高强度合并范围",
            },
            "enable_message_debounce": {"type": "bool", "label": "消息收口"},
            "enable_smart_message_debounce": {"type": "bool", "label": "智能文本收口"},
            "smart_message_debounce_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "智能收口等待秒数"},
            "text_message_debounce_seconds": {"type": "float", "min": 0.0, "max": 15.0, "label": "文本补话等待秒数"},
            "text_message_debounce_max_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "文本最长等待秒数"},
            "message_debounce_max_merge_messages": {"type": "int", "min": 0, "max": 30, "label": "最大合并消息数"},
            "enable_smart_silence": {"type": "bool", "label": "智能沉默"},
            "smart_silence_judge_mode": {
                "type": "select",
                "choices": {"boundary_only", "contextual"},
                "aliases": {
                    "边界": "boundary_only",
                    "明确边界": "boundary_only",
                    "保守": "boundary_only",
                    "上下文": "contextual",
                    "模型判断": "contextual",
                    "更智能": "contextual",
                    "智能": "contextual",
                },
                "label": "智能沉默判断模式",
            },
            "smart_silence_min_confidence": {"type": "percent", "min": 0.0, "max": 1.0, "label": "智能沉默最低置信度"},
            "smart_silence_model_timeout_seconds": {"type": "float", "min": 0.2, "max": 5.0, "label": "智能沉默模型超时秒数"},
            "enable_passive_response_review": {"type": "bool", "label": "被动回复复核"},
            "enable_framework_error_leak_guard": {"type": "bool", "label": "框架异常文本外发拦截"},
            "enable_proactive_message_review": {"type": "bool", "label": "主动消息终审"},
            "enable_proactive_burst": {"type": "bool", "label": "主动消息爆发式发送"},
            "proactive_burst_max_messages": {"type": "int", "min": 2, "max": 3, "label": "爆发式发送最多消息数"},
            "proactive_burst_gap_min_seconds": {"type": "int", "min": 10, "max": 600, "label": "爆发消息最小间隔秒数"},
            "proactive_burst_gap_max_seconds": {"type": "int", "min": 20, "max": 900, "label": "爆发消息最大间隔秒数"},
            "proactive_hour_activity_curve": {"type": "string", "max_len": 240, "label": "主动小时活跃曲线"},
            "passive_review_mode": {
                "type": "select",
                "choices": {"local_only", "severe_only", "full"},
                "aliases": {
                    "本地": "local_only",
                    "本地复核": "local_only",
                    "仅本地": "local_only",
                    "严重": "severe_only",
                    "严重问题": "severe_only",
                    "默认": "severe_only",
                    "完整": "full",
                    "全量": "full",
                    "积极": "full",
                },
                "label": "被动回复复核模式",
            },
            "passive_review_strength": {"type": "select", "choices": {"lenient", "balanced", "strict"}, "label": "被动回复复核强度"},
            "proactive_review_mode": {"type": "select", "choices": {"local_only", "severe_only", "full"}, "label": "主动消息终审模式"},
            "proactive_review_strength": {"type": "select", "choices": {"lenient", "balanced", "strict"}, "label": "主动消息终审强度"},
            "response_review_max_chars": {"type": "int", "min": 80, "max": 900, "label": "被动复核长度阈值"},
            "reply_style_prompt": {"type": "string", "max_len": 1200, "label": "回复风格约束"},
            "enable_group_wakeup_question": {"type": "bool", "label": "群聊解惑唤醒"},
            "group_wakeup_question_threshold": {"type": "int", "min": 0, "max": 100, "label": "解惑强度阈值"},
            "group_wakeup_short_text_wait_seconds": {"type": "float", "min": 0.0, "max": 30.0, "label": "短唤醒补话等待秒数"},
            "group_wakeup_cooldown_seconds": {"type": "int", "min": 0, "max": 3600, "label": "群聊唤醒冷却秒数"},
            "enable_rest_reply_simulation": {"type": "bool", "label": "休息回复闸门"},
            "rest_reply_mode": {
                "type": "select",
                "choices": {"probability", "llm"},
                "aliases": {
                    "概率": "probability",
                    "仅概率": "probability",
                    "概率模式": "probability",
                    "模型": "llm",
                    "模型判断": "llm",
                    "llm_judge": "llm",
                    "model": "llm",
                },
                "label": "休息回复闸门模式",
            },
            "rest_reply_probability": {"type": "percent", "min": 0.0, "max": 1.0, "label": "休息闸门概率"},
            "rest_reply_llm_threshold": {"type": "int", "min": 0, "max": 100, "label": "休息醒来模型阈值"},
            "rest_reply_awake_grace_minutes": {"type": "int", "min": 0, "max": 240, "label": "休息清醒宽限分钟"},
            "enable_rest_backlog_reply": {"type": "bool", "label": "醒后补看私聊"},
            "rest_backlog_max_messages": {"type": "int", "min": 1, "max": 12, "label": "醒后最多补看条数"},
            "enable_busy_reply_gate": {"type": "bool", "label": "繁忙回复闸门"},
            "busy_reply_min_delay_seconds": {"type": "int", "min": 0, "max": 900, "label": "繁忙回复最短延迟秒数"},
            "busy_reply_max_delay_seconds": {"type": "int", "min": 0, "max": 900, "label": "繁忙回复最长延迟秒数"},
            "busy_reply_proactive_resume_buffer_minutes": {"type": "int", "min": 0, "max": 120, "label": "忙完后主动缓冲分钟数"},
            "enable_health_state": {"type": "bool", "label": "健康/不适状态"},
            "enable_hunger_state": {"type": "bool", "label": "饥饿/胃口状态"},
            "enable_cycle_state": {"type": "bool", "label": "生理期模拟"},
            "humanized_state_intensity": {"type": "int", "min": 0, "max": 100, "label": "拟人状态强度"},
            "natural_language_photo_generation_mode": {
                "type": "select",
                "choices": {"tool_first", "rule_fast", "off"},
                "aliases": {
                    "工具": "tool_first",
                    "工具优先": "tool_first",
                    "tool": "tool_first",
                    "规则": "rule_fast",
                    "规则快判": "rule_fast",
                    "快判": "rule_fast",
                    "关闭": "off",
                    "关": "off",
                },
                "label": "非指令生图处理方式",
            },
            "enable_natural_language_photo_generation": {"type": "bool", "label": "允许规则快判生图/改图"},
            "photo_generation_private_owner_max_daily": {"type": "int", "min": -1, "max": 100, "label": "主要用户私聊生图每日上限"},
            "photo_generation_private_friend_max_daily": {"type": "int", "min": -1, "max": 100, "label": "其他陪伴用户私聊生图每日上限"},
            "photo_generation_group_max_daily": {"type": "int", "min": -1, "max": 100, "label": "群聊生图每日上限"},
            "photo_generation_proactive_max_daily": {"type": "int", "min": -1, "max": 100, "label": "Bot 主动生图每日上限"},
            "command_photo_generation_max_daily": {"type": "int", "min": -1, "max": 100, "label": "用户请求生图每日上限"},
            "photo_generation_trace_max_size_kb": {"type": "int", "min": 0, "max": 102400, "label": "生图日志单文件大小（KB）"},
            "photo_generation_trace_backup_count": {"type": "int", "min": 0, "max": 20, "label": "生图日志轮转备份数"},
            "natural_language_photo_generation_max_daily": {"type": "int", "min": 0, "max": 100, "label": "规则快判生图每日上限"},
            "natural_language_photo_extra_prompt": {"type": "string", "max_len": 5000, "label": "规则快判生图附加提示词"},
            "enable_backup_external_image_api": {"type": "bool", "label": "启用备选在线图片 API"},
            "external_image_download_proxy": {"type": "string", "max_len": 500, "label": "在线图片结果下载代理"},
            "external_image_download_use_environment_proxy": {"type": "bool", "label": "在线图片下载继承环境代理"},
            "enable_photo_reference_image": {"type": "bool", "label": "启用人设/穿搭参考图一致性"},
            "external_image_api_platform": {
                "type": "select",
                "choices": {"auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "bailian", "modelscope", "doubao", "gemini"},
                "aliases": {
                    "openrouter": "openrouter",
                    "open-router": "openrouter",
                    "open_router": "openrouter",
                    "openrouter.ai": "openrouter",
                    "agnes-ai": "agnes",
                    "sapiens": "agnes",
                    "Agnes": "agnes",
                    "日日新": "sensenova",
                    "商汤日日新": "sensenova",
                    "百炼": "bailian",
                    "阿里云百炼": "bailian",
                    "魔搭": "modelscope",
                    "魔搭社区": "modelscope",
                    "豆包": "doubao",
                    "火山": "doubao",
                    "火山引擎": "doubao",
                    "seedream": "doubao",
                    "google": "gemini",
                    "谷歌": "gemini",
                    "openai兼容": "openai",
                    "minimaxi": "minimax",
                    "minimax-ai": "minimax",
                    "海螺": "minimax",
                    "海螺ai": "minimax",
                },
                "label": "在线生图平台",
            },
            "backup_external_image_api_platform": {
                "type": "select",
                "choices": {"auto", "openai", "openrouter", "agnes", "sensenova", "minimax", "bailian", "modelscope", "doubao", "gemini"},
                "aliases": {
                    "openrouter": "openrouter",
                    "open-router": "openrouter",
                    "open_router": "openrouter",
                    "openrouter.ai": "openrouter",
                    "agnes-ai": "agnes",
                    "sapiens": "agnes",
                    "Agnes": "agnes",
                    "日日新": "sensenova",
                    "商汤日日新": "sensenova",
                    "百炼": "bailian",
                    "阿里云百炼": "bailian",
                    "魔搭": "modelscope",
                    "魔搭社区": "modelscope",
                    "豆包": "doubao",
                    "火山": "doubao",
                    "火山引擎": "doubao",
                    "seedream": "doubao",
                    "google": "gemini",
                    "谷歌": "gemini",
                    "openai兼容": "openai",
                    "minimaxi": "minimax",
                    "minimax-ai": "minimax",
                    "海螺": "minimax",
                    "海螺ai": "minimax",
                },
                "label": "备选在线生图平台",
            },
            "backup_external_image_api_timeout_seconds": {"type": "int", "min": 20, "max": 600, "label": "备选在线生图超时秒数"},
            "enable_qzone_comment_inbox": {"type": "bool", "label": "QQ 空间评论收件箱"},
            "qzone_comment_inbox_interval_minutes": {"type": "int", "min": 5, "max": 1440, "label": "空间评论检查间隔"},
            "qzone_comment_inbox_recent_posts": {"type": "int", "min": 1, "max": 20, "label": "空间评论扫描说说数"},
            "qzone_comment_inbox_max_replies_per_tick": {"type": "int", "min": 1, "max": 5, "label": "空间评论每轮最多回复"},
        }

    def _companion_manual_config_label(self, key: str) -> str:
        spec = self._companion_manual_config_specs().get(str(key or ""))
        if isinstance(spec, dict):
            return str(spec.get("label") or key)
        meta = self._companion_manual_config_display_meta().get(str(key or ""))
        return str(meta.get("label") or key) if isinstance(meta, dict) else str(key or "")

    def _companion_manual_config_display_meta(self) -> dict[str, dict[str, str]]:
        return {
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID": {"label": "群聊连续对话判断模型", "location": "拓展页 -> 模型/Provider -> GROUP_FOLLOWUP_JUDGE_PROVIDER_ID"},
            "FAST_RESPONSE_PROVIDER_ID": {"label": "快速响应模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 快速响应模型"},
            "COMPLEX_REASONING_PROVIDER_ID": {"label": "复杂推理模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 复杂推理模型"},
            "CREATIVE_MODEL_PROVIDER_ID": {"label": "创作模型", "location": "拓展页 -> 模型/Provider -> 快速配置 -> 创作模型"},
            "LLM_PROVIDER_ID": {"label": "插件主模型 Provider", "location": "拓展页 -> 模型/Provider -> LLM_PROVIDER_ID"},
            "MAI_STYLE_PROVIDER_ID": {"label": "风格/轻量任务模型", "location": "拓展页 -> 模型/Provider -> MAI_STYLE_PROVIDER_ID"},
            "PHOTO_MODEL_PROVIDER_ID": {"label": "生图模型感知 Provider", "location": "拓展页 -> 模型/Provider -> PHOTO_MODEL_PROVIDER_ID"},
            "PHOTO_PROMPT_PROVIDER_ID": {"label": "生图提示词模型", "location": "拓展页 -> 模型/Provider -> PHOTO_PROMPT_PROVIDER_ID"},
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID": {"label": "主动人格判定模型", "location": "拓展页 -> 模型/Provider -> PROACTIVE_PERSONA_JUDGE_PROVIDER_ID"},
            "RESPONSE_REVIEW_PROVIDER_ID": {"label": "回复复核模型", "location": "拓展页 -> 模型/Provider -> RESPONSE_REVIEW_PROVIDER_ID"},
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID": {"label": "智能收口小模型", "location": "拓展页 -> 模型/Provider -> SMART_MESSAGE_DEBOUNCE_PROVIDER_ID；也可在 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口 查看"},
            "SMART_SILENCE_PROVIDER_ID": {"label": "智能沉默模型", "location": "拓展页 -> 模型/Provider -> SMART_SILENCE_PROVIDER_ID；也可在 功能开关 -> 通用能力 -> 智能沉默 查看"},
            "TROUBLESHOOTING_PROVIDER_ID": {"label": "插件答疑/排障模型", "location": "拓展页 -> 模型/Provider -> TROUBLESHOOTING_PROVIDER_ID"},
            "enable_group_wakeup_enhancement": {"label": "群聊唤醒增强", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强"},
            "group_access_mode": {"label": "群聊访问模式", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊启用范围"},
            "group_wakeup_context_words": {"label": "群聊弱相关唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_direct_words": {"label": "群聊强唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_owner_direct_words": {"label": "主要用户专属强唤醒词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 唤醒词"},
            "group_wakeup_interest_keywords": {"label": "群聊兴趣唤醒关键词", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 兴趣唤醒"},
            "reply_style_prompt": {"label": "回复风格约束", "location": "拓展页 -> 世界知识/角色与表达 -> 回复风格约束；也可在配置页搜索 reply_style_prompt"},
            "enable_smart_silence": {"label": "智能沉默", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_judge_mode": {"label": "智能沉默判断模式", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_min_confidence": {"label": "智能沉默最低置信度", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "smart_silence_model_timeout_seconds": {"label": "智能沉默模型超时秒数", "location": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默"},
            "enable_passive_response_review": {"label": "被动回复复核", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核"},
            "enable_framework_error_leak_guard": {"label": "框架异常文本外发拦截", "location": "拓展页 -> 功能开关 -> 通用能力 -> 框架异常文本外发拦截"},
            "passive_review_mode": {"label": "被动回复复核模式", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "passive_review_strength": {"label": "被动回复复核强度", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "enable_proactive_message_review": {"label": "主动消息终审", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审"},
            "proactive_review_mode": {"label": "主动消息终审模式", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情"},
            "proactive_review_strength": {"label": "主动消息终审强度", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情"},
            "response_review_max_chars": {"label": "被动复核长度阈值", "location": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情"},
            "enable_rest_reply_simulation": {"label": "休息回复闸门", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_mode": {"label": "休息回复闸门模式", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_probability": {"label": "休息闸门概率", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_llm_threshold": {"label": "休息醒来模型阈值", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_reply_awake_grace_minutes": {"label": "休息清醒宽限分钟", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_rest_backlog_reply": {"label": "醒后补看私聊", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "rest_backlog_max_messages": {"label": "醒后最多补看条数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门"},
            "enable_busy_reply_gate": {"label": "繁忙回复闸门", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_min_delay_seconds": {"label": "繁忙回复最短延迟秒数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_max_delay_seconds": {"label": "繁忙回复最长延迟秒数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "busy_reply_proactive_resume_buffer_minutes": {"label": "忙完后主动缓冲分钟数", "location": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门"},
            "enable_health_state": {"label": "健康/不适状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_hunger_state": {"label": "饥饿/胃口状态", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态"},
            "enable_cycle_state": {"label": "生理期模拟", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟"},
            "humanized_state_intensity": {"label": "拟人状态强度", "location": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度"},
            "enable_photo_text_action": {"label": "生图/拍照能力", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力"},
            "enable_photo_reference_image": {"label": "参考图一致性", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 参考图一致性"},
            "photo_generation_backend": {"label": "生图后端", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 后端选择"},
            "external_image_api_platform": {"label": "在线生图平台", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "EXTERNAL_IMAGE_API_BASE_URL": {"label": "在线图片 API 地址", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "EXTERNAL_IMAGE_API_MODEL": {"label": "在线图片模型", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "external_image_download_proxy": {"label": "在线图片结果下载代理", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "external_image_download_use_environment_proxy": {"label": "在线图片下载继承环境代理", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 在线图片 API"},
            "enable_backup_external_image_api": {"label": "启用备选在线图片 API", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_platform": {"label": "备选在线生图平台", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": {"label": "备选在线 API 地址", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": {"label": "备选在线图片模型", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_size": {"label": "备选在线生图尺寸", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "backup_external_image_api_timeout_seconds": {"label": "备选在线生图超时秒数", "location": "拓展页 -> 模型配置 -> 生图模型 -> 在线 API 队列"},
            "photo_reference_catalog": {"label": "规范参考图目录", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 参考图库；也可用命令 陪伴 参考图 / 参考图库"},
            "natural_language_photo_generation_mode": {"label": "非指令生图处理方式", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图"},
            "natural_language_photo_extra_prompt": {"label": "规则快判生图附加提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图"},
            "photo_generation_prompt_format": {"label": "生图提示词表达方式", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_scene_presets": {"label": "生图场景预设", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_fixed_prompt": {"label": "全局固定生图提示词（兼容）", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_text2img_fixed_prompt": {"label": "文生图固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_selfie_fixed_prompt": {"label": "自拍/人像固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "photo_generation_edit_fixed_prompt": {"label": "改图固定提示词", "location": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 画面风格"},
            "enable_qzone_integration": {"label": "QQ 空间联动", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动"},
            "enable_qzone_life_publish": {"label": "QQ 空间生活说说", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_max_daily": {"label": "每日自动说说上限", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_window_mode": {"label": "生活说说发布时段模式", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_windows": {"label": "生活说说自定义窗口", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_allow_insomnia_night": {"label": "失眠时允许凌晨说说", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_intra_day_gap_minutes": {"label": "同日说说最小间隔", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "qzone_life_publish_similarity_threshold": {"label": "生活说说去重相似阈值", "location": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 生活说说"},
            "WEB_EXPLORATION_API_BASE_URL": {"label": "主动搜索接口地址", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_KEY": {"label": "主动搜索接口 API Key", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "WEB_EXPLORATION_API_MODEL": {"label": "主动搜索接口模型", "location": "拓展页 -> 功能开关 -> 长线主动 -> 主动搜索详情 -> 自定义搜索接口"},
            "max_daily_messages": {"label": "主动消息每日上限", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "min_interval_minutes": {"label": "主动消息最小间隔", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "enable_proactive_burst": {"label": "主动消息爆发式发送", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_max_messages": {"label": "爆发式发送最多消息数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_gap_min_seconds": {"label": "爆发消息最小间隔秒数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_burst_gap_max_seconds": {"label": "爆发消息最大间隔秒数", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "proactive_hour_activity_curve": {"label": "主动小时活跃曲线", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动节奏实验参数"},
            "quiet_hours": {"label": "主动免打扰时间", "location": "拓展页 -> 功能开关 -> 长线主动/私聊陪伴 -> 主动消息相关参数"},
            "target_user_ids": {"label": "目标用户 ID 列表", "location": "拓展页 -> 模块 -> 快速启动 -> 部署与目标 -> 私聊服务对象 ID"},
            "REST_WAKEUP_PROVIDER_ID": {"label": "休息醒来判断模型", "location": "拓展页 -> 模型/Provider -> REST_WAKEUP_PROVIDER_ID"},
            "enable_companion_memory": {"label": "本地陪伴画像", "location": "拓展页 -> 功能开关 -> 本地画像、通用表达与习惯 -> 本地陪伴画像"},
            "enable_group_episode_memory": {"label": "群聊片段记忆", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊片段记忆"},
            "enable_group_privacy_guard": {"label": "群聊隐私保护", "location": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊隐私保护"},
        }

    def _companion_manual_config_location(self, key: str) -> str:
        key = str(key or "").strip()
        locations = {
            "enable_group_companion": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关",
            "enable_group_conversation_followup": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "group_conversation_followup_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "group_conversation_followup_max_turns": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊陪伴总开关详情 -> 场景与续接",
            "enable_group_high_intensity_mode": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口",
            "group_high_intensity_wakeup_window_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_wakeup_threshold": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_cooldown_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_merge_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_max_merge_messages": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "group_high_intensity_merge_scope": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊高强度收口详情 -> 关联参数",
            "enable_message_debounce": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖",
            "enable_smart_message_debounce": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口",
            "smart_message_debounce_wait_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 智能文本收口",
            "text_message_debounce_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "text_message_debounce_max_wait_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "message_debounce_max_merge_messages": "拓展页 -> 功能开关 -> 通用能力 -> 消息收口防抖详情 -> 补话等待",
            "enable_smart_silence": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_judge_mode": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_min_confidence": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "smart_silence_model_timeout_seconds": "拓展页 -> 功能开关 -> 通用能力 -> 智能沉默",
            "enable_passive_response_review": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核",
            "enable_framework_error_leak_guard": "拓展页 -> 功能开关 -> 通用能力 -> 框架异常文本外发拦截",
            "passive_review_mode": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "passive_review_strength": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "enable_proactive_message_review": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审",
            "proactive_review_mode": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情",
            "proactive_review_strength": "拓展页 -> 功能开关 -> 私聊陪伴 -> 主动消息终审详情",
            "response_review_max_chars": "拓展页 -> 功能开关 -> 私聊陪伴 -> 被动回复复核详情",
            "reply_style_prompt": "拓展页 -> 世界知识/角色与表达 -> 回复风格约束；也可在配置页搜索 reply_style_prompt",
            "enable_group_wakeup_question": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 解惑与冷群",
            "group_wakeup_question_threshold": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 解惑与冷群",
            "group_wakeup_short_text_wait_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 节流与拟人感",
            "group_wakeup_cooldown_seconds": "拓展页 -> 功能开关 -> 群聊观察 -> 群聊唤醒增强详情 -> 节流与拟人感",
            "enable_rest_reply_simulation": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_mode": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_probability": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_llm_threshold": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_reply_awake_grace_minutes": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "enable_rest_backlog_reply": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "rest_backlog_max_messages": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 休息回复闸门",
            "enable_busy_reply_gate": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_min_delay_seconds": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_max_delay_seconds": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "busy_reply_proactive_resume_buffer_minutes": "拓展页 -> 功能开关 -> 拟人状态/休息 -> 繁忙回复闸门",
            "enable_health_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_hunger_state": "拓展页 -> 功能开关 -> 拟人状态 -> 身体状态",
            "enable_cycle_state": "拓展页 -> 功能开关 -> 拟人状态 -> 生理期模拟",
            "humanized_state_intensity": "拓展页 -> 功能开关 -> 拟人状态 -> 状态强度",
            "natural_language_photo_generation_mode": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "enable_natural_language_photo_generation": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "photo_generation_private_owner_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 用户请求生图",
            "photo_generation_private_friend_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 用户请求生图",
            "photo_generation_group_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 用户请求生图",
            "photo_generation_proactive_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 用户请求生图",
            "command_photo_generation_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 用户请求生图",
            "photo_generation_trace_max_size_kb": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图可观测日志",
            "photo_generation_trace_backup_count": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 生图可观测日志",
            "natural_language_photo_generation_max_daily": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "natural_language_photo_extra_prompt": "拓展页 -> 功能开关 -> 长线主动 -> 生图/拍照能力详情 -> 非指令生图/改图",
            "enable_qzone_comment_inbox": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_interval_minutes": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_recent_posts": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
            "qzone_comment_inbox_max_replies_per_tick": "拓展页 -> 功能开关 -> 长线主动 -> QQ 空间联动详情 -> 评论收件箱",
        }
        meta = self._companion_manual_config_display_meta().get(key)
        if isinstance(meta, dict) and meta.get("location"):
            return str(meta.get("location"))
        return locations.get(key, "拓展页 -> 功能开关，搜索参数名或中文名")

    def _companion_manual_config_ref(self, key: str, *, include_location: bool = True) -> str:
        key = str(key or "").strip()
        if not key:
            return ""
        label = self._companion_manual_config_label(key)
        text = f"{label}（{key}）" if label and label != key else key
        if include_location:
            text = f"{text}｜位置：{self._companion_manual_config_location(key)}"
        return text

    def _companion_manual_mentioned_config_keys(self, text: str) -> list[str]:
        source = str(text or "")
        if not source:
            return []
        found: list[str] = []
        keys = set(self._companion_manual_config_specs()) | set(self._companion_manual_config_display_meta())
        for key in sorted(keys, key=len, reverse=True):
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", source):
                found.append(key)
        labels: list[tuple[str, str]] = []
        for key in keys:
            label = self._companion_manual_config_label(key)
            if label and label != key:
                labels.append((key, label))
        for key, label in sorted(labels, key=lambda item: len(item[1]), reverse=True):
            if key not in found and label in source:
                found.append(key)
        return found

    def _companion_manual_config_aliases(self) -> dict[str, str]:
        aliases = {
            "群聊陪伴": "enable_group_companion",
            "连续对话保持": "enable_group_conversation_followup",
            "续接窗口": "group_conversation_followup_seconds",
            "连续对话窗口": "group_conversation_followup_seconds",
            "群聊续接窗口": "group_conversation_followup_seconds",
            "续接轮数": "group_conversation_followup_max_turns",
            "连续对话轮数": "group_conversation_followup_max_turns",
            "续接上限": "group_conversation_followup_max_turns",
            "高强度收口": "enable_group_high_intensity_mode",
            "高强度阈值": "group_high_intensity_wakeup_threshold",
            "高强度唤醒阈值": "group_high_intensity_wakeup_threshold",
            "高强度持续": "group_high_intensity_cooldown_seconds",
            "收口持续": "group_high_intensity_cooldown_seconds",
            "高强度合并等待": "group_high_intensity_merge_seconds",
            "合并等待": "group_high_intensity_merge_seconds",
            "高强度合并范围": "group_high_intensity_merge_scope",
            "合并范围": "group_high_intensity_merge_scope",
            "文本等待": "text_message_debounce_seconds",
            "文本补话等待": "text_message_debounce_seconds",
            "智能等待": "smart_message_debounce_wait_seconds",
            "智能收口等待": "smart_message_debounce_wait_seconds",
            "文本最长等待": "text_message_debounce_max_wait_seconds",
            "最大合并数": "message_debounce_max_merge_messages",
            "智能沉默": "enable_smart_silence",
            "智能静默": "enable_smart_silence",
            "智能沉默模式": "smart_silence_judge_mode",
            "沉默判断模式": "smart_silence_judge_mode",
            "沉默模型判断": "smart_silence_judge_mode",
            "沉默置信度": "smart_silence_min_confidence",
            "智能沉默置信度": "smart_silence_min_confidence",
            "沉默模型超时": "smart_silence_model_timeout_seconds",
            "智能沉默超时": "smart_silence_model_timeout_seconds",
            "回复复核": "enable_passive_response_review",
            "被动复核": "enable_passive_response_review",
            "框架异常拦截": "enable_framework_error_leak_guard",
            "异常文本外发拦截": "enable_framework_error_leak_guard",
            "主动复核": "enable_proactive_message_review",
            "复核模式": "passive_review_mode",
            "回复复核模式": "passive_review_mode",
            "主动复核模式": "proactive_review_mode",
            "被动复核阈值": "response_review_max_chars",
            "复核长度阈值": "response_review_max_chars",
            "回复风格": "reply_style_prompt",
            "回复风格约束": "reply_style_prompt",
            "表达风格": "reply_style_prompt",
            "简洁回复要求": "reply_style_prompt",
            "求助阈值": "group_wakeup_question_threshold",
            "解惑阈值": "group_wakeup_question_threshold",
            "短唤醒等待": "group_wakeup_short_text_wait_seconds",
            "休息闸门": "enable_rest_reply_simulation",
            "休息回复闸门": "enable_rest_reply_simulation",
            "睡眠闸门": "enable_rest_reply_simulation",
            "晚安不回": "enable_rest_reply_simulation",
            "休息模式": "rest_reply_mode",
            "休息闸门模式": "rest_reply_mode",
            "休息概率": "rest_reply_probability",
            "休息回复概率": "rest_reply_probability",
            "休息模型阈值": "rest_reply_llm_threshold",
            "醒来阈值": "rest_reply_llm_threshold",
            "清醒宽限": "rest_reply_awake_grace_minutes",
            "休息清醒宽限": "rest_reply_awake_grace_minutes",
            "醒后补看": "enable_rest_backlog_reply",
            "醒后补看条数": "rest_backlog_max_messages",
            "繁忙闸门": "enable_busy_reply_gate",
            "繁忙回复闸门": "enable_busy_reply_gate",
            "忙碌回复闸门": "enable_busy_reply_gate",
            "繁忙最短延迟": "busy_reply_min_delay_seconds",
            "繁忙最长延迟": "busy_reply_max_delay_seconds",
            "忙完主动缓冲": "busy_reply_proactive_resume_buffer_minutes",
            "繁忙主动缓冲": "busy_reply_proactive_resume_buffer_minutes",
            "健康状态": "enable_health_state",
            "不适状态": "enable_health_state",
            "饥饿状态": "enable_hunger_state",
            "饥饿模拟": "enable_hunger_state",
            "胃口状态": "enable_hunger_state",
            "生理期": "enable_cycle_state",
            "生理期模拟": "enable_cycle_state",
            "拟人状态强度": "humanized_state_intensity",
            "身体状态强度": "humanized_state_intensity",
            "非指令生图": "natural_language_photo_generation_mode",
            "自然语言生图": "natural_language_photo_generation_mode",
            "自然语言改图": "natural_language_photo_generation_mode",
            "规则快判生图": "enable_natural_language_photo_generation",
            "规则快判改图": "enable_natural_language_photo_generation",
            "主要用户私聊生图上限": "photo_generation_private_owner_max_daily",
            "主用户私聊生图上限": "photo_generation_private_owner_max_daily",
            "其他陪伴用户私聊生图上限": "photo_generation_private_friend_max_daily",
            "其他用户私聊生图上限": "photo_generation_private_friend_max_daily",
            "群聊生图上限": "photo_generation_group_max_daily",
            "群聊生图每日上限": "photo_generation_group_max_daily",
            "Bot主动生图上限": "photo_generation_proactive_max_daily",
            "Bot 主动生图上限": "photo_generation_proactive_max_daily",
            "用户生图上限": "command_photo_generation_max_daily",
            "用户请求生图上限": "command_photo_generation_max_daily",
            "指令生图上限": "command_photo_generation_max_daily",
            "陪伴生图上限": "command_photo_generation_max_daily",
            "生图日志大小": "photo_generation_trace_max_size_kb",
            "生图日志轮转数": "photo_generation_trace_backup_count",
            "生图日志备份数": "photo_generation_trace_backup_count",
            "自然生图上限": "natural_language_photo_generation_max_daily",
            "自然语言生图上限": "natural_language_photo_generation_max_daily",
            "规则快判生图上限": "natural_language_photo_generation_max_daily",
            "自然生图附加提示词": "natural_language_photo_extra_prompt",
            "自然语言生图附加提示词": "natural_language_photo_extra_prompt",
            "规则快判生图附加提示词": "natural_language_photo_extra_prompt",
            "文生图固定提示词": "photo_generation_text2img_fixed_prompt",
            "自拍固定提示词": "photo_generation_selfie_fixed_prompt",
            "人像固定提示词": "photo_generation_selfie_fixed_prompt",
            "改图固定提示词": "photo_generation_edit_fixed_prompt",
            "全局固定生图提示词": "photo_generation_fixed_prompt",
            "备选生图api": "enable_backup_external_image_api",
            "备选生图API": "enable_backup_external_image_api",
            "备选在线api": "enable_backup_external_image_api",
            "备选在线API": "enable_backup_external_image_api",
            "生图下载代理": "external_image_download_proxy",
            "图片下载代理": "external_image_download_proxy",
            "生图使用系统代理": "external_image_download_use_environment_proxy",
            "图片下载使用系统代理": "external_image_download_use_environment_proxy",
            "备选生图平台": "backup_external_image_api_platform",
            "生图平台": "external_image_api_platform",
            "备选生图超时": "backup_external_image_api_timeout_seconds",
            "空间评论收件箱": "enable_qzone_comment_inbox",
            "空间评论间隔": "qzone_comment_inbox_interval_minutes",
            "空间评论扫描数": "qzone_comment_inbox_recent_posts",
            "空间每轮回复数": "qzone_comment_inbox_max_replies_per_tick",
        }
        for key in self._companion_manual_config_specs():
            aliases[key] = key
            label = self._companion_manual_config_label(key)
            if label:
                aliases[label] = key
        return aliases

    def _companion_manual_config_key_from_alias(self, value: Any) -> str:
        text = str(value or "").strip()
        if text in self._companion_manual_config_specs():
            return text
        compact = re.sub(r"\s+", "", text).lower()
        for alias, key in self._companion_manual_config_aliases().items():
            if re.sub(r"\s+", "", str(alias or "")).lower() == compact:
                return key
        return ""

    def _companion_manual_config_keys_from_alias_text(self, value: Any, *, limit: int = 6) -> list[str]:
        compact = re.sub(r"\s+", "", str(value or "")).lower()
        if not compact:
            return []
        found: list[str] = []
        for alias, key in sorted(self._companion_manual_config_aliases().items(), key=lambda item: len(str(item[0])), reverse=True):
            alias_compact = re.sub(r"\s+", "", str(alias or "")).lower()
            if not alias_compact or len(alias_compact) < 2:
                continue
            if alias_compact in compact and key not in found:
                found.append(key)
                if len(found) >= limit:
                    break
        return found

    def _companion_manual_issue_tags(self, query: str) -> set[str]:
        compact = re.sub(r"\s+", "", str(query or "")).lower()
        tags: set[str] = set()
        if not compact:
            return tags
        if any(word in compact for word in ("刚才", "这次", "刚刚", "上一条", "为什么没回", "为什么不回", "没有回复", "不回复", "没回复")):
            tags.add("recent")
        if any(word in compact for word in ("群聊", "群里", "群内", "群消息", "没@", "没at", "连续对话", "高强度", "收口", "唤醒", "插话", "碰瓷")):
            tags.add("group")
        if any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")):
            tags.add("followup")
        if any(word in compact for word in ("高强度", "收口", "合并", "压制")):
            tags.add("high_intensity")
        if any(word in compact for word in ("防抖", "智能收口", "补话", "等补充", "合并消息")):
            tags.add("debounce")
        if any(word in compact for word in ("休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "醒后补看")):
            tags.add("rest")
        if any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊")):
            tags.add("silence")
        if any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截")):
            tags.add("review")
        if any(word in compact for word in ("生图", "画图", "改图", "自拍", "参考图", "穿搭图", "出图", "提示词", "图片生成", "自然语言生图")):
            tags.add("photo")
        if any(word in compact for word in ("qq空间", "空间", "说说", "评论", "点赞", "cookie", "onebot", "登录空间")):
            tags.add("qzone")
        if any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "拟人状态")):
            tags.add("state")
        if any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格")):
            tags.add("style")
        if any(word in compact for word in ("模型", "provider", "llm", "超时", "timeout", "降级", "无有效json", "无效json")):
            tags.add("model")
        if any(word in compact for word in ("rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "知识图谱", "专属记忆", "未安装")):
            tags.add("memory")
        if any(word in compact for word in ("管理员命令", "管理权限", "管理员权限", "指令失效", "命令失效", "用不了命令", "不能用命令", "夹层密码", "输出夹层密码", "强制输出", "admins_id", "target_user_ids", "umo", "uid", "default")):
            tags.add("permission")
        if any(word in compact for word in ("在哪", "哪里", "位置", "设置", "配置项", "怎么改", "如何改", "调参")):
            tags.add("location")
        return tags

    def _is_companion_manual_natural_permission_question(self, text: Any) -> bool:
        compact = re.sub(r"\s+", "", str(text or "")).lower()
        if not compact:
            return False
        permission_terms = (
            "管理员命令", "管理命令", "管理权限", "管理员权限", "私聊管理员", "私聊管理",
            "夹层密码", "资料柜密码", "抽屉密码", "输出夹层密码", "强制输出", "重置夹层密码",
            "admins_id", "adminsid", "target_user_ids", "targetuserids", "umo", "uid", "default",
        )
        problem_terms = (
            "用不了", "不能用", "没法用", "无法用", "失效", "不生效", "没反应", "不识别",
            "怎么用", "怎么设置", "怎么配置", "怎么加", "为什么", "咋", "哪", "填什么", "要填",
        )
        if not any(term in compact for term in permission_terms):
            return False
        return any(term in compact for term in problem_terms)

    async def _maybe_answer_companion_manual_natural_question(self, event: AstrMessageEvent, text: Any) -> bool:
        if not self._is_companion_manual_natural_permission_question(text):
            return False
        question = self._companion_manual_clean_question_text(text, 260)
        answer = await self._companion_manual_answer(event, question)
        await self._reply(event, answer)
        try:
            event.stop_event()
        except Exception:
            pass
        logger.info("[PrivateCompanion] 自然语言插件权限答疑已接管: text=%s", _single_line(question, 120))
        return True

    def _companion_manual_entry_tags(self, entry: dict[str, Any]) -> set[str]:
        title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
        if "被动消息为什么没回" in title or "被动未回复" in title:
            return {"recent"}
        if "连续对话" in title:
            return {"group", "followup"}
        if "高强度" in title:
            return {"group", "high_intensity"}
        if "收口" in title or "防抖" in title:
            return {"debounce"}
        if "唤醒" in title or "答疑误触" in title:
            return {"group"}
        if "休息" in title or "晚安" in title:
            return {"rest"}
        if "智能沉默" in title:
            return {"silence"}
        if "回复复核" in title or "去重" in title or "复读" in title:
            return {"review"}
        if "回复太长" in title or "字数限制" in title:
            return {"style"}
        if "模型" in title or "provider" in title:
            return {"model"}
        if "rememberyou" in title or "联动" in title:
            return {"memory"}
        if "管理命令" in title or "权限" in title or "夹层密码" in title:
            return {"permission"}
        if "主动消息" in title:
            return {"proactive"}
        if "拟人身体" in title or "饥饿" in title:
            return {"state"}
        if "生图" in title or "自拍" in title:
            return {"photo"}
        if "qq空间" in title or "空间" in title:
            return {"qzone"}
        if "群聊老是" in title:
            return {"group"}
        return set()

    def _companion_manual_current_config_value(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        return _flat_get(getattr(self, "config", None), key, None)

    def _companion_manual_parse_bool(self, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
            return True
        if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否"}:
            return False
        return None

    def _companion_manual_normalize_config_value(self, key: str, value: Any) -> tuple[bool, Any, str]:
        spec = self._companion_manual_config_specs().get(str(key or ""))
        if not isinstance(spec, dict):
            return False, None, f"不允许通过答疑命令修改配置项：{key}"
        kind = str(spec.get("type") or "string")
        try:
            if kind == "bool":
                parsed = self._companion_manual_parse_bool(value)
                if parsed is None:
                    return False, None, "布尔值请使用 开启/关闭、true/false、1/0。"
                return True, parsed, ""
            if kind == "int":
                parsed = int(float(str(value).strip()))
                parsed = max(int(spec.get("min", 0)), min(int(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "float":
                parsed = float(str(value).strip())
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", parsed)), parsed))
                return True, parsed, ""
            if kind == "percent":
                text = str(value or "").strip().replace("%", "")
                parsed = float(text)
                if parsed > 1:
                    parsed = parsed / 100.0
                parsed = max(float(spec.get("min", 0.0)), min(float(spec.get("max", 1.0)), parsed))
                return True, parsed, ""
            if kind == "select":
                text = str(value or "").strip().lower()
                aliases = spec.get("aliases") if isinstance(spec.get("aliases"), dict) else {}
                text = str(aliases.get(text, text))
                choices = spec.get("choices") if isinstance(spec.get("choices"), set) else set()
                if text not in choices:
                    return False, None, f"可选值只有：{', '.join(sorted(str(item) for item in choices))}"
                return True, text, ""
            if kind == "string":
                text = str(value or "").strip()
                max_len = _safe_int(spec.get("max_len"), 1200, 1)
                if len(text) > max_len:
                    text = text[:max_len].strip()
                return True, text, ""
        except (TypeError, ValueError):
            return False, None, f"{self._companion_manual_config_label(key)} 的值格式不对。"
        return False, None, f"不支持的配置类型：{kind}"

    def _companion_manual_values_equal(self, left: Any, right: Any) -> bool:
        if isinstance(left, bool) or isinstance(right, bool):
            return bool(left) == bool(right)
        try:
            return abs(float(left) - float(right)) < 0.0001
        except (TypeError, ValueError):
            return str(left) == str(right)

    def _companion_manual_format_config_value(self, value: Any) -> str:
        if isinstance(value, bool):
            return "开启" if value else "关闭"
        if isinstance(value, str) and len(value) > 120:
            return _single_line(value, 120)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _companion_manual_format_config_item_value(self, key: str, value: Any) -> str:
        if str(key or "") in {
            "command_photo_generation_max_daily",
            *PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values(),
        }:
            limit = _safe_int(value, -1, -1, 100)
            if limit < 0:
                return "-1（不限量）"
            if limit == 0:
                return "0（不允许）"
            return f"{limit} 次"
        if str(key or "") in {"rest_reply_probability", "smart_silence_min_confidence"}:
            try:
                number = float(value)
                percent = number * 100 if 0 <= number <= 1 else number
                return f"{percent:.0f}%"
            except (TypeError, ValueError):
                return self._companion_manual_format_config_value(value)
        return self._companion_manual_format_config_value(value)

    def _companion_manual_confidence_label(self, confidence: Any) -> str:
        score = _safe_float(confidence, 0.0, 0.0)
        if score >= 0.78:
            return "高"
        if score >= 0.55:
            return "中"
        return "低"

    def _companion_manual_add_proposal(
        self,
        proposals: list[dict[str, Any]],
        key: str,
        value: Any,
        reason: str,
        *,
        evidence: list[str] | None = None,
        strength: str = "可尝试",
        confidence: float = 0.62,
    ) -> None:
        if any(item.get("key") == key for item in proposals):
            return
        ok, normalized, error = self._companion_manual_normalize_config_value(key, value)
        if not ok:
            logger.debug("[PrivateCompanion] 答疑可执行建议被跳过: key=%s error=%s", key, _single_line(error, 120))
            return
        old = self._companion_manual_current_config_value(key)
        if self._companion_manual_values_equal(old, normalized):
            return
        evidence_lines = [
            _single_line(item, 150)
            for item in (evidence or [])
            if _single_line(item, 150)
        ]
        current_evidence = f"当前 {key}={self._companion_manual_format_config_item_value(key, old)}"
        if not any(str(item).startswith(f"当前 {key}=") for item in evidence_lines):
            evidence_lines.insert(0, current_evidence)
        proposals.append(
            {
                "key": key,
                "label": self._companion_manual_config_label(key),
                "old": old,
                "value": normalized,
                "reason": _single_line(reason, 160),
                "evidence": evidence_lines[:4],
                "strength": _single_line(strength, 20) or "可尝试",
                "confidence": max(0.0, min(1.0, _safe_float(confidence, 0.0, 0.0))),
            }
        )

    def _companion_manual_build_config_proposals(
        self,
        question: str,
        selected: list[dict[str, Any]],
        event: AstrMessageEvent | None = None,
    ) -> list[dict[str, Any]]:
        query = str(question or "")
        compact = re.sub(r"\s+", "", query).lower()
        titles = " ".join(str(item.get("title") or "") for item in selected)
        titles_compact = re.sub(r"\s+", "", titles).lower()
        primary_title = re.sub(r"\s+", "", str(selected[0].get("title") if selected else "")).lower()
        proposals: list[dict[str, Any]] = []
        runtime = self._companion_manual_runtime_snapshot(event) if event is not None else ""

        def runtime_evidence(*patterns: str) -> list[str]:
            lines = []
            for line in runtime.splitlines():
                if not line:
                    continue
                if any(pattern and pattern in line for pattern in patterns):
                    lines.append(line)
            return lines[:2]

        def current_number(key: str, default: float = 0.0) -> float:
            return _safe_float(self._companion_manual_current_config_value(key), default, 0.0)

        def current_int(key: str, default: int = 0) -> int:
            return _safe_int(self._companion_manual_current_config_value(key), default, 0)

        def current_bool(key: str, default: bool = False) -> bool:
            value = self._companion_manual_current_config_value(key)
            parsed = self._companion_manual_parse_bool(value)
            return bool(default) if parsed is None else parsed

        issue_tags = self._companion_manual_issue_tags(query)
        recent_question = "recent" in issue_tags or any(word in compact for word in ("刚才", "刚刚", "这次", "上一条", "为什么没回", "为什么不回", "没回复", "没发出来"))
        recent_no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=4) if event is not None and recent_question else []
        recent_no_reply_compact = re.sub(r"\s+", "", " ".join(recent_no_reply)).lower()

        group_issue_words = ("群聊不回复", "群里不回复", "群内不回复", "群聊没回复", "群里没回复", "群聊回复慢", "群里回复慢", "好久才回复", "老是不回复")
        group_slow = any(word in compact for word in group_issue_words) or primary_title.startswith("群聊老是不回复")
        followup = any(word in compact for word in ("连续对话", "续接", "接话", "没@", "没at")) or primary_title.startswith("群聊连续对话")
        high_intensity = any(word in compact for word in ("高强度", "收口", "合并", "压制")) or primary_title.startswith("群聊高强度")
        debounce = any(word in compact for word in ("防抖", "智能收口", "补话", "等待", "合并消息")) or primary_title.startswith("消息收口")
        wakeup_mistouch = any(word in compact for word in ("误触", "碰瓷", "插话", "乱回复", "抢话"))
        wakeup_mistouch = wakeup_mistouch or any(word in compact for word in ("太敏感", "过于敏感", "容易触发", "乱触发"))
        photo_mistouch = any(word in compact for word in ("生图误触", "画图误触", "改图误触", "自然语言生图怎么关闭")) or (
            "生图" in compact and any(word in compact for word in ("误触", "太敏感", "过于敏感", "容易触发", "乱触发"))
        )
        qzone_repeat = any(word in compact for word in ("空间重复", "一直回复", "重复回复", "评论重复")) or ("qq空间" in titles_compact and "重复" in compact)
        rest_gate = any(word in compact for word in ("休息闸门", "睡眠闸门", "休息回复", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "不回复消息", "闸门"))
        smart_silence = any(word in compact for word in ("智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话"))
        response_review_issue = any(word in compact for word in ("回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截"))
        verbose_reply = any(word in compact for word in ("话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "回复风格", "口语化"))
        body_state = any(word in compact for word in ("饥饿", "饿", "胃口", "生理期", "来月经", "姨妈", "健康状态", "不适状态", "情绪太低", "情绪过低", "状态太低", "拟人状态"))
        photo_behavior = any(word in compact for word in ("生图没反应", "生图没有反应", "出图后", "好了", "自然语言生图", "自拍", "参考图", "穿搭图", "提示词"))
        qzone_setup = any(word in compact for word in ("空间首次", "第一次使用", "登录空间", "先登录空间", "点赞失效", "空间点赞", "cookie", "onebot")) or ("qq空间" in titles_compact and not qzone_repeat)
        if recent_no_reply_compact:
            focused_tags = issue_tags - {"recent", "location"}

            def recent_focus_allows(tag: str) -> bool:
                return not focused_tags or tag in focused_tags

            if recent_focus_allows("rest") and ("休息闸门" in recent_no_reply_compact or "休息静默" in recent_no_reply_compact):
                rest_gate = True
            if recent_focus_allows("silence") and "智能沉默" in recent_no_reply_compact:
                smart_silence = True
            if recent_focus_allows("group") and "群聊答疑复核" in recent_no_reply_compact:
                wakeup_mistouch = True
            if recent_focus_allows("review") and ("回复复核去重" in recent_no_reply_compact or "发送前去重" in recent_no_reply_compact):
                response_review_issue = True
            if recent_focus_allows("debounce") and ("智能收口" in recent_no_reply_compact or "消息收口" in recent_no_reply_compact):
                debounce = True

        def propose(
            key: str,
            value: Any,
            reason: str,
            scene: str,
            *,
            condition: str = "",
            strength: str = "可尝试",
            confidence: float = 0.62,
            runtime_patterns: tuple[str, ...] = (),
        ) -> None:
            evidence = [f"命中场景：{scene}"]
            if condition:
                evidence.append(condition)
            evidence.extend(runtime_evidence(*runtime_patterns))
            self._companion_manual_add_proposal(
                proposals,
                key,
                value,
                reason,
                evidence=evidence,
                strength=strength,
                confidence=confidence,
            )

        if group_slow or followup:
            if not current_bool("enable_group_conversation_followup", True):
                propose(
                    "enable_group_conversation_followup",
                    True,
                    "开启后，明确叫过 Bot 的同一用户在短窗口内不用每句都 @。",
                    "群聊不回复/连续对话续接",
                    condition="连续对话当前关闭，未 @ 的后续消息更容易断开。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前群连续对话锚点", "最近群唤醒"),
                )
            seconds = current_int("group_conversation_followup_seconds", 120)
            if seconds < 90 or seconds > 240:
                propose(
                    "group_conversation_followup_seconds",
                    120,
                    "把续接窗口收在 120 秒左右，既不太迟钝，也不容易很久后误认。",
                    "群聊不回复/连续对话续接",
                    condition=f"当前续接窗口 {seconds} 秒不在推荐观察区间 90-240 秒。",
                    strength="强建议" if seconds <= 0 or seconds > 360 else "可尝试",
                    confidence=0.76 if seconds <= 0 or seconds > 360 else 0.66,
                    runtime_patterns=("当前群连续对话锚点", "最近群消息"),
                )
            turns = current_int("group_conversation_followup_max_turns", 1)
            if turns < 1:
                propose(
                    "group_conversation_followup_max_turns",
                    1,
                    "至少允许无 @ 续接一轮，能改善“叫过之后马上不回”的体感。",
                    "群聊不回复/连续对话续接",
                    condition="当前无 @ 续接轮数为 0，明确叫过 Bot 后也不会自然续接。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("当前群连续对话锚点",),
                )
            elif group_slow and turns == 1 and "更容易" in compact:
                propose(
                    "group_conversation_followup_max_turns",
                    2,
                    "如果目标是更容易接住同一人的后续补话，可以临时放到 2 轮观察。",
                    "用户明确希望更容易接话",
                    condition="当前最多续接 1 轮，调到 2 会增加对同一用户补话的承接。",
                    strength="可尝试",
                    confidence=0.58,
                    runtime_patterns=("当前群连续对话锚点",),
                )

        if group_slow or high_intensity:
            if not current_bool("enable_group_high_intensity_mode", True):
                propose(
                    "enable_group_high_intensity_mode",
                    True,
                    "开启后连续叫 Bot 会先合并，避免多次 LLM 并发挤爆主链。",
                    "群聊高频唤醒/回复慢",
                    condition="高强度收口当前关闭，连续 @ 时更容易形成多轮并发。",
                    strength="可尝试",
                    confidence=0.6,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_wakeup_threshold", 3) < 4:
                propose(
                    "group_high_intensity_wakeup_threshold",
                    4,
                    "阈值从 3 提到 4，可以减少普通连续对话过早进入高强度压制。",
                    "高强度收口过早/回复慢",
                    condition="当前阈值低于 4，普通连续互动也可能较早进入收口。",
                    strength="可尝试",
                    confidence=0.66,
                    runtime_patterns=("当前群高强度", "最近群唤醒"),
                )
            if current_int("group_high_intensity_cooldown_seconds", 150) > 90:
                propose(
                    "group_high_intensity_cooldown_seconds",
                    90,
                    "收口持续时间缩短到 90 秒，能让群聊更快回到正常续接判断。",
                    "高强度收口持续过久",
                    condition="当前持续时间超过 90 秒，容易让一段时间内的续接判断偏保守。",
                    strength="可尝试",
                    confidence=0.65,
                    runtime_patterns=("当前群高强度",),
                )
            if current_int("group_high_intensity_merge_seconds", 8) > 5:
                propose(
                    "group_high_intensity_merge_seconds",
                    5,
                    "高强度合并等待降到 5 秒，能少一点“好久才回”的体感。",
                    "高强度合并等待偏长",
                    condition="当前合并等待超过 5 秒，会直接增加高强度期间首条回复等待。",
                    strength="可尝试",
                    confidence=0.7,
                    runtime_patterns=("当前群高强度",),
                )
            if str(self._companion_manual_current_config_value("group_high_intensity_merge_scope") or "group") == "group":
                propose(
                    "group_high_intensity_merge_scope",
                    "same_user",
                    "只合并同一发送者的补话，避免别人接话时被全群收口卷进去。",
                    "高强度合并范围过宽",
                    condition="当前按全群合并，其他人接话也可能被并入同一轮。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近群消息", "当前群高强度"),
                )

        if group_slow or debounce:
            if current_number("text_message_debounce_seconds", 0.0) > 2:
                propose(
                    "text_message_debounce_seconds",
                    2,
                    "普通文本固定等待降到 2 秒，能减少完整发言后的无谓等待。",
                    "消息收口导致回复慢",
                    condition="当前普通文本固定等待超过 2 秒。",
                    strength="强建议",
                    confidence=0.78,
                    runtime_patterns=("最近智能收口",),
                )
            if current_bool("enable_smart_message_debounce", False) and current_number("smart_message_debounce_wait_seconds", 3.0) > 2:
                propose(
                    "smart_message_debounce_wait_seconds",
                    2,
                    "智能收口的总等待预算降到 2 秒，保留补话感但不拖太久。",
                    "智能收口等待偏长",
                    condition="智能收口已开启，且等待预算超过 2 秒。",
                    strength="可尝试",
                    confidence=0.69,
                    runtime_patterns=("最近智能收口",),
                )
            if current_number("text_message_debounce_max_wait_seconds", 12.0) > 10:
                propose(
                    "text_message_debounce_max_wait_seconds",
                    10,
                    "滑动收口最长等待压到 10 秒，避免用户连续补话时一直拖住回复。",
                    "收口最长等待偏长",
                    condition="当前文本最长等待超过 10 秒。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近智能收口",),
                )

        if wakeup_mistouch and not photo_mistouch and not smart_silence:
            if current_bool("enable_group_wakeup_question", True) and current_int("group_wakeup_question_threshold", 65) < 75:
                propose(
                    "group_wakeup_question_threshold",
                    75,
                    "提高公共求助阈值，能减少普通闲聊被当成“需要 Bot 答疑”。",
                    "群聊答疑/解惑误触",
                    condition="用户问题包含误触/碰瓷/乱插话意图，且当前求助阈值低于 75。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近群唤醒", "最近被动未回复"),
                )
            if current_number("group_wakeup_short_text_wait_seconds", 15.0) < 5:
                propose(
                    "group_wakeup_short_text_wait_seconds",
                    5,
                    "短唤醒多等几秒补话，能减少一两个字就触发回复。",
                    "短文本唤醒误触",
                    condition="短唤醒补话等待低于 5 秒，碎片消息更容易提前触发。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近群唤醒",),
                )

        if rest_gate:
            if current_bool("enable_rest_reply_simulation", False) and any(word in compact for word in ("误触", "太敏感", "容易", "晚安", "说句晚安", "不回")):
                propose(
                    "enable_rest_reply_simulation",
                    False,
                    "先关闭休息回复闸门，可以避免一句晚安后整段被动消息都被睡眠状态挡掉。",
                    "休息回复闸门误触/晚安后不回",
                    condition="用户问题命中休息/晚安不回，且休息回复闸门当前开启。",
                    strength="强建议",
                    confidence=0.86,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )
            elif current_bool("enable_rest_reply_simulation", False) and str(self._companion_manual_current_config_value("rest_reply_mode") or "") == "probability":
                propose(
                    "rest_reply_mode",
                    "llm",
                    "把休息闸门从纯概率切到模型判断，能减少普通晚安被机械挡住。",
                    "休息回复闸门误触",
                    condition="当前是 probability 模式，容易给人随机不回的体感。",
                    strength="可尝试",
                    confidence=0.68,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_rest_reply_simulation", False) and current_int("rest_reply_awake_grace_minutes", 30) < 45:
                propose(
                    "rest_reply_awake_grace_minutes",
                    60,
                    "清醒宽限调到 60 分钟，刚被叫醒后的一小段对话不容易再次被当作睡眠中。",
                    "休息回复闸门反复拦截",
                    condition="当前清醒宽限低于 45 分钟。",
                    strength="可尝试",
                    confidence=0.63,
                    runtime_patterns=("最近被动未回复", "休息待补看私聊"),
                )

        if smart_silence:
            if current_bool("enable_smart_silence", True) and any(word in compact for word in ("误触", "太敏感", "不该沉默", "没回", "不回")):
                threshold = current_number("smart_silence_min_confidence", 0.66)
                if threshold < 0.76:
                    propose(
                        "smart_silence_min_confidence",
                        0.78,
                        "提高沉默置信度，只有更确定是用户想结束话题时才取消回复。",
                        "智能沉默误触",
                        condition=f"当前智能沉默置信度 {threshold:.2f} 低于 0.76。",
                        strength="强建议",
                        confidence=0.78,
                        runtime_patterns=("最近被动未回复",),
                    )
                else:
                    propose(
                        "enable_smart_silence",
                        False,
                        "先关掉智能沉默止血，确认误触样本后再重新打开调阈值。",
                        "智能沉默误触",
                        condition="用户明确反馈沉默误触，且阈值已经不低。",
                        strength="可尝试",
                        confidence=0.61,
                        runtime_patterns=("最近被动未回复",),
                    )

        if response_review_issue:
            if current_bool("enable_passive_response_review", True) and any(word in compact for word in ("关闭", "关掉", "不要", "先关")):
                propose(
                    "enable_passive_response_review",
                    False,
                    "先关闭被动回复复核可以止血，主动消息终审不会受影响。",
                    "用户明确要求关闭被动复核",
                    condition="问题里明确出现关闭/不要复核，且被动复核当前开启。",
                    strength="可尝试",
                    confidence=0.72,
                    runtime_patterns=("最近被动未回复",),
                )
            mode = str(self._companion_manual_current_config_value("passive_review_mode") or "severe_only").strip()
            if current_bool("enable_passive_response_review", True) and mode == "full":
                propose(
                    "passive_review_mode",
                    "severe_only",
                    "从 full 调回 severe_only，可以保留严重问题保护，同时减少普通被动回复被过度改写或误拦截。",
                    "回复复核过强/误杀",
                    condition="当前复核模式是 full，普通短回复也更容易进入模型复核。",
                    strength="强建议",
                    confidence=0.76,
                    runtime_patterns=("最近被动未回复",),
                )
            if current_bool("enable_passive_response_review", True) and current_int("response_review_max_chars", 260) < 220:
                propose(
                    "response_review_max_chars",
                    260,
                    "把被动复核长度阈值调回 260 字附近，避免很短的正常闲聊频繁进入复核链。",
                    "被动复核长度阈值偏低",
                    condition="当前被动复核长度阈值低于 220 字。",
                    strength="可尝试",
                    confidence=0.64,
                    runtime_patterns=("最近被动未回复",),
                )

        if verbose_reply:
            style_text = str(self._companion_manual_current_config_value("reply_style_prompt") or "").strip()
            concise_style = (
                "每次回复至多三句话；简单回答尽量保持在 1-2 句，口语化、简洁，跟随当前对话节奏；"
                "必须使用简体中文，符合社交媒体聊天习惯。需要排障、教程、复杂说明或用户明确要求详细解释时，可以优先保证信息完整。"
            )
            if "至多三句" not in style_text and "至多三句话" not in style_text and "1-2" not in style_text:
                propose(
                    "reply_style_prompt",
                    concise_style,
                    "把简洁、口语化和复杂问题例外写进统一回复风格，能压住高强度群聊里动态提示词带来的话痨倾向。",
                    "回复太长/回复风格不生效",
                    condition="当前回复风格没有检测到明确的句数/简洁约束。",
                    strength="强建议",
                    confidence=0.79,
                )

        if photo_mistouch:
            if str(self._companion_manual_current_config_value("natural_language_photo_generation_mode") or "tool_first").strip().lower() == "rule_fast":
                propose(
                    "natural_language_photo_generation_mode",
                    "tool_first",
                    "把非指令生图改回工具优先，让普通聊天先进入主链，只有模型明确调用 pc_generate_photo 时才生图，可减少和闲聊或其他生图插件抢触发。",
                    "非指令生图误触",
                    condition="用户问题明确提到生图/改图误触，且当前使用规则快判前置接管。",
                    strength="强建议",
                    confidence=0.86,
                )
            if current_bool("enable_natural_language_photo_generation", False):
                propose(
                    "enable_natural_language_photo_generation",
                    False,
                    "关闭规则快判后，插件不会在主链前直接抢高置信生图请求；显式指令和 pc_generate_photo 工具仍可正常使用。",
                    "规则快判生图误触",
                    condition="用户问题明确提到生图/改图误触，且规则快判入口当前开启。",
                    strength="可尝试",
                    confidence=0.78,
                )

        if photo_behavior:
            command_quota_question = any(word in compact for word in ("上限", "额度")) and any(
                marker in compact for marker in ("用户请求", "指令生图", "陪伴生图", "陪伴自拍", "陪伴改图", "额度用完")
            )
            command_photo_limit = _safe_int(
                self._companion_manual_current_config_value("command_photo_generation_max_daily"),
                -1,
                -1,
                100,
            )
            if command_quota_question and command_photo_limit >= 0:
                propose(
                    "command_photo_generation_max_daily",
                    -1,
                    "把用户请求生图每日上限设为 -1，显式陪伴生图指令与 pc_generate_photo 工具调用都不再受每日次数限制；0 表示完全不允许用户请求生图。",
                    "用户请求生图上限",
                    condition="用户明确询问指令/工具生图额度，并希望取消每日限制。",
                    strength="可尝试",
                    confidence=0.86,
                )
            rule_quota_question = "上限" in compact and any(
                marker in compact for marker in ("规则快判", "自然语言生图", "非指令生图", "rule_fast")
            )
            if rule_quota_question and current_int("natural_language_photo_generation_max_daily", 2) < 100:
                propose(
                    "natural_language_photo_generation_max_daily",
                    100,
                    "把规则快判每日上限放宽到 100，适合测试期观察插件前置接管和出图链路。",
                    "规则快判生图上限",
                    condition="用户问题提到规则快判/自然语言生图上限，且当前上限低于 100。",
                    strength="可尝试",
                    confidence=0.74,
                )
            current_mode = str(self._companion_manual_current_config_value("natural_language_photo_generation_mode") or "tool_first").strip().lower()
            if any(word in compact for word in ("呆", "好了", "没反应", "没有反应")) and current_mode == "off":
                propose(
                    "natural_language_photo_generation_mode",
                    "tool_first",
                    "如果希望普通聊天里说“画一张/发自拍”能触发生图，先用工具优先：主链理解意图后调用 pc_generate_photo，不会像规则快判那样抢普通对话。",
                    "非指令生图没有反应",
                    condition="当前非指令生图处理方式为 off。",
                    strength="可尝试",
                    confidence=0.72,
                )

        if qzone_repeat:
            if current_bool("enable_qzone_comment_inbox", False):
                propose(
                    "enable_qzone_comment_inbox",
                    False,
                    "先暂停评论收件箱，避免排障前继续对同一条评论公开回复。",
                    "QQ 空间评论重复回复",
                    condition="用户问题明确提到空间评论重复/一直回复，先关入口可止血。",
                    strength="强建议",
                    confidence=0.83,
                )
            if current_int("qzone_comment_inbox_interval_minutes", 60) < 60:
                propose(
                    "qzone_comment_inbox_interval_minutes",
                    60,
                    "评论检查间隔至少 60 分钟，降低重复扫描带来的二次回复风险。",
                    "QQ 空间评论重复回复",
                    condition="当前评论检查间隔小于 60 分钟，重复扫描频率偏高。",
                    strength="可尝试",
                    confidence=0.62,
                )
            if current_int("qzone_comment_inbox_max_replies_per_tick", 1) > 1:
                propose(
                    "qzone_comment_inbox_max_replies_per_tick",
                    1,
                    "每轮最多回复 1 条，排障时更容易定位是哪条评论触发。",
                    "QQ 空间评论重复回复",
                    condition="当前每轮可回复多条，排障时不容易定位触发源。",
                    strength="可尝试",
                    confidence=0.6,
                )

        if qzone_setup and current_bool("enable_qzone_comment_inbox", False) and current_int("qzone_comment_inbox_interval_minutes", 60) < 30:
            propose(
                "qzone_comment_inbox_interval_minutes",
                30,
                "首次接入空间时先把评论轮询间隔放到 30 分钟以上，更容易观察 Cookie 和去重是否稳定。",
                "QQ 空间首次使用/点赞评论排障",
                condition="评论收件箱已开启，且当前轮询间隔偏短。",
                strength="可尝试",
                confidence=0.58,
                runtime_patterns=("最近排障测试",),
            )

        if body_state:
            if any(word in compact for word in ("一天都是饥饿", "总是饥饿", "一直饿", "老是饿", "一直饥饿")) and current_bool("enable_hunger_state", True):
                propose(
                    "enable_hunger_state",
                    False,
                    "先关闭饥饿状态止血，避免状态机持续把吃饭/胃口写进回复。",
                    "饥饿状态长期不退",
                    condition="用户反馈一天都是饥饿状态，且饥饿状态当前开启。",
                    strength="强建议",
                    confidence=0.82,
                    runtime_patterns=("当前用户状态",),
                )
            if any(word in compact for word in ("生理期", "来月经", "姨妈")) and any(word in compact for word in ("不要", "关闭", "不想", "误触", "没设置", "奇怪")) and current_bool("enable_cycle_state", True):
                propose(
                    "enable_cycle_state",
                    False,
                    "关闭后会清理生理期相关状态，避免未确认用户接受时继续注入。",
                    "生理期模拟不想启用",
                    condition="用户问题包含生理期模拟负反馈，且开关当前开启。",
                    strength="强建议",
                    confidence=0.8,
                    runtime_patterns=("最近被动未回复",),
                )
            if any(word in compact for word in ("情绪太低", "情绪过低", "状态太低", "太容易过低")) and current_int("humanized_state_intensity", 50) > 35:
                propose(
                    "humanized_state_intensity",
                    35,
                    "降低拟人状态强度，能让健康、饥饿、情绪余波这类状态少一点压过人格。",
                    "拟人状态过强",
                    condition="用户反馈情绪/状态过低，且当前状态强度高于 35。",
                    strength="可尝试",
                    confidence=0.69,
                )

        primary_tags = issue_tags

        def proposal_rank(item: dict[str, Any]) -> float:
            key = str(item.get("key") or "")
            score = _safe_float(item.get("confidence"), 0.0, 0.0) * 100
            if str(item.get("strength") or "") == "强建议":
                score += 18
            if recent_no_reply_compact and any(pattern in recent_no_reply_compact for pattern in ("休息闸门", "智能沉默", "回复复核", "群聊答疑复核")):
                if (
                    ("休息闸门" in recent_no_reply_compact and (key.startswith("rest_") or key == "enable_rest_reply_simulation"))
                    or ("智能沉默" in recent_no_reply_compact and (key.startswith("smart_silence") or key == "enable_smart_silence"))
                    or ("回复复核" in recent_no_reply_compact and key in {"enable_passive_response_review", "passive_review_mode", "passive_review_strength", "response_review_max_chars"})
                    or ("群聊答疑复核" in recent_no_reply_compact and key in {"group_wakeup_question_threshold", "enable_group_wakeup_question"})
                ):
                    score += 22
            if "rest" in primary_tags and (key.startswith("rest_") or key in {"enable_rest_reply_simulation", "enable_rest_backlog_reply"}):
                score += 16
            if "silence" in primary_tags and (key.startswith("smart_silence") or key == "enable_smart_silence"):
                score += 16
            if "review" in primary_tags and key in {"enable_passive_response_review", "passive_review_mode", "passive_review_strength", "response_review_max_chars"}:
                score += 16
            if "photo" in primary_tags and ("photo" in key or "image" in key):
                score += 16
            if "state" in primary_tags and key in {"enable_health_state", "enable_hunger_state", "enable_cycle_state", "humanized_state_intensity"}:
                score += 16
            if "style" in primary_tags and key == "reply_style_prompt":
                score += 16
            if "qzone" in primary_tags and (key.startswith("qzone_") or key == "enable_qzone_comment_inbox"):
                score += 16
            return score

        proposals.sort(key=proposal_rank, reverse=True)
        return proposals[:6]

    def _companion_manual_pending_key(self, event: AstrMessageEvent) -> str:
        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        group_id = ""
        try:
            group_id = self._extract_group_id_from_event(event)
        except Exception:
            group_id = ""
        return f"group:{group_id}:{sender_id}" if group_id else f"private:{sender_id}"

    def _companion_manual_pending_store(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        store = data.setdefault("manual_diagnosis_pending_config", {})
        if not isinstance(store, dict):
            store = {}
            data["manual_diagnosis_pending_config"] = store
        return store

    def _companion_manual_prune_pending_store(self, store: dict[str, Any]) -> None:
        if not isinstance(store, dict):
            return
        now = _now_ts()
        for key, item in list(store.items()):
            ts = _safe_float(item.get("ts") if isinstance(item, dict) else 0.0, 0.0, 0.0)
            if ts <= 0 or now - ts > 1800:
                store.pop(key, None)
        if len(store) <= 80:
            return
        ranked = sorted(
            store.items(),
            key=lambda pair: _safe_float(pair[1].get("ts") if isinstance(pair[1], dict) else 0.0, 0.0, 0.0),
            reverse=True,
        )
        keep = {key for key, _ in ranked[:80]}
        for key in list(store.keys()):
            if key not in keep:
                store.pop(key, None)

    def _companion_manual_store_pending_config(
        self,
        event: AstrMessageEvent,
        question: str,
        proposals: list[dict[str, Any]],
    ) -> str:
        store = self._companion_manual_pending_store()
        self._companion_manual_prune_pending_store(store)
        key = self._companion_manual_pending_key(event)
        if not proposals:
            if key in store:
                store.pop(key, None)
                self._save_data_sync(sections={"manual_diagnosis_pending_config"})
            return ""
        token = uuid.uuid4().hex[:6]
        store[key] = {
            "token": token,
            "ts": _now_ts(),
            "question": _single_line(question, 260),
            "changes": proposals,
        }
        self._save_data_sync(sections={"manual_diagnosis_pending_config"})
        return token

    def _companion_manual_recent_context_store(self) -> dict[str, Any]:
        data = getattr(self, "data", None)
        if not isinstance(data, dict):
            return {}
        store = data.setdefault("manual_diagnosis_recent_context", {})
        if not isinstance(store, dict):
            store = {}
            data["manual_diagnosis_recent_context"] = store
        now = _now_ts()
        for key, item in list(store.items()):
            if not isinstance(item, dict) or now - _safe_float(item.get("ts"), 0.0, 0.0) > 20 * 60:
                store.pop(key, None)
        if len(store) > 80:
            ranked = sorted(
                store.items(),
                key=lambda pair: _safe_float(pair[1].get("ts") if isinstance(pair[1], dict) else 0.0, 0.0, 0.0),
                reverse=True,
            )
            keep = {key for key, _ in ranked[:80]}
            for key in list(store.keys()):
                if key not in keep:
                    store.pop(key, None)
        return store

    def _companion_manual_recent_context_text(self, event: AstrMessageEvent) -> str:
        store = self._companion_manual_recent_context_store()
        item = store.get(self._companion_manual_pending_key(event))
        if not isinstance(item, dict):
            return ""
        question = _single_line(item.get("question"), 180)
        answer = _single_line(item.get("answer"), 360)
        configs = item.get("configs") if isinstance(item.get("configs"), list) else []
        config_text = "；".join(_single_line(value, 120) for value in configs[:4] if _single_line(value, 120))
        parts = []
        if question:
            parts.append(f"上一轮问题：{question}")
        if answer:
            parts.append(f"上一轮答复摘要：{answer}")
        if config_text:
            parts.append(f"上一轮涉及配置：{config_text}")
        return "\n".join(parts)

    async def _companion_manual_media_context(self, event: AstrMessageEvent, question: str) -> str:
        sources: list[tuple[str, str]] = []

        def add(source: Any, label: str) -> None:
            text = str(source or "").strip()
            if not text:
                return
            if any(existing == text for existing, _ in sources):
                return
            sources.append((text, label))

        try:
            sender_id = str(event.get_sender_id())
        except Exception:
            sender_id = ""
        current_getter = getattr(self, "_photo_reference_sources_from_current_event", None)
        if callable(current_getter):
            try:
                for source in await current_getter(event, sender_id):
                    add(source, "随消息携带图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑携带图片提取失败: %s", _single_line(exc, 120))
        reply_cache_getter = getattr(self, "_photo_reference_sources_from_reply_cache", None)
        if callable(reply_cache_getter):
            try:
                for source in reply_cache_getter(event):
                    add(source, "引用撤回/缓存图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑引用缓存图片提取失败: %s", _single_line(exc, 120))
        reply_getter = getattr(self, "_photo_reference_sources_from_reply_event", None)
        if callable(reply_getter):
            try:
                for source in await reply_getter(event):
                    add(source, "引用消息图片")
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑引用图片提取失败: %s", _single_line(exc, 120))
        if not sources:
            return ""

        limited_sources = sources[:5]
        source_values = [source for source, _label in limited_sources]
        labels: list[str] = []
        for _source, label in limited_sources:
            if label not in labels:
                labels.append(label)
        vision_text = ""
        transcriber = getattr(self, "_transcribe_private_inbound_images", None)
        if callable(transcriber):
            try:
                raw_vision = await transcriber(
                    source_values,
                    umo=str(getattr(event, "unified_msg_origin", "") or ""),
                    user_text=question or "陪伴答疑图片排障",
                    force_contextual=True,
                )
                limit_getter = getattr(self, "_private_image_vision_text_limit", None)
                limit = limit_getter(len(source_values)) if callable(limit_getter) else 1200
                vision_text = _single_line(raw_vision, _safe_int(limit, 1200, 240, 2400))
            except Exception as exc:
                logger.info("[PrivateCompanion] 答疑图片视觉摘要失败: %s", _single_line(exc, 120))
                vision_text = ""

        lines = [
            "本轮答疑附带图片上下文：",
            f"图片来源：{'、'.join(labels)}；数量={len(source_values)}",
        ]
        if vision_text:
            lines.append("图片视觉摘要：" + vision_text)
        else:
            lines.append("已检测到图片，但当前没有拿到可靠视觉摘要；如果用户问截图内容，只能说明需要更清晰图片或日志，不要编造画面。")
        return "\n".join(lines)

    def _companion_manual_store_recent_context(
        self,
        event: AstrMessageEvent,
        *,
        question: str,
        answer: str,
        proposals: list[dict[str, Any]],
    ) -> None:
        store = self._companion_manual_recent_context_store()
        key = self._companion_manual_pending_key(event)
        configs = [
            self._companion_manual_config_ref(_single_line(item.get("key"), 80), include_location=False)
            for item in proposals[:6]
            if isinstance(item, dict) and _single_line(item.get("key"), 80)
        ]
        store[key] = {
            "ts": _now_ts(),
            "question": _single_line(question, 260),
            "answer": _single_line(answer, 600),
            "configs": configs,
        }
        self._companion_manual_recent_context_store()
        try:
            self._schedule_data_save(sections={"manual_diagnosis_recent_context"})
        except Exception:
            try:
                self._save_data_sync(sections={"manual_diagnosis_recent_context"})
            except Exception:
                pass

    def _companion_manual_format_config_proposals(self, token: str, proposals: list[dict[str, Any]]) -> str:
        if not proposals:
            return ""
        lines = ["可执行建议（现在还没改配置）："]
        for idx, item in enumerate(proposals, start=1):
            confidence = _safe_float(item.get("confidence"), 0.0, 0.0)
            key = _single_line(item.get("key"), 80)
            evidence = [
                _single_line(part, 120)
                for part in (item.get("evidence") if isinstance(item.get("evidence"), list) else [])
                if _single_line(part, 120)
            ]
            lines.append(
                f"{idx}. {self._companion_manual_config_ref(key)}："
                f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}；"
                f"{item.get('strength') or '可尝试'}｜置信度{self._companion_manual_confidence_label(confidence)}；"
                f"{item.get('reason')}"
            )
            if evidence:
                lines.append("   依据：" + "；".join(evidence[:3]))
        lines.append("")
        lines.append("确认执行：陪伴 答疑确认")
        lines.append("取消建议：陪伴 答疑取消")
        lines.append("手动改一项：陪伴 答疑设置 <配置项> <值>")
        if token:
            lines.append(f"本次建议编号：{token}")
        return "\n".join(lines)

    def _companion_manual_format_config_proposals_brief(self, token: str, proposals: list[dict[str, Any]]) -> str:
        if not proposals:
            return ""
        lines = ["可直接调整的项（还没改）："]
        for idx, item in enumerate(proposals[:3], start=1):
            key = _single_line(item.get("key"), 80)
            if not key:
                continue
            lines.append(
                f"{idx}. {self._companion_manual_config_ref(key)}："
                f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}"
            )
        if not lines[1:]:
            return ""
        lines.append("要我直接应用的话发：陪伴 答疑确认；不想改就发：陪伴 答疑取消。")
        if token:
            lines.append(f"建议编号：{token}")
        return "\n".join(lines)

    def _companion_manual_fallback_answer(
        self,
        event: AstrMessageEvent,
        question: str,
        selected: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
        media_context: str = "",
    ) -> str:
        query = _single_line(question, 180)
        if not selected:
            if media_context:
                return (
                    "我这轮已经检测到你带了图片或引用了图片，但答疑模型没有给出稳定诊断。"
                    "如果图片摘要没生成，就需要再看清晰截图或对应日志；如果摘要已生成，可以继续追问“这张图里哪里不对”。"
                )
            return (
                "这句我还没抓准你想查哪块功能。你可以直接说具体一点，比如“刚才为什么没回复”、"
                "“为什么等了几秒”、或“某个配置在哪里改”，我就能按当前会话状态接着查。"
            )
        primary = selected[0] if isinstance(selected[0], dict) else {}
        title = _single_line(primary.get("title"), 60) or "相关功能"
        summary = _single_line(primary.get("summary"), 220) or "这类情况需要结合当前运行状态判断。"
        group_note = self._companion_manual_current_group_note(event)
        checks = [str(item) for item in primary.get("checks", []) if str(item or "").strip()]
        suggestions = [str(item) for item in primary.get("suggestions", []) if str(item or "").strip()]
        no_reply = self._companion_manual_recent_no_reply_evidence(event, limit=2)
        tests = self._companion_manual_recent_test_evidence(limit=2)
        lines = []
        lines.append(f"我先按“{title}”看，{summary}")
        if group_note:
            lines.append(_single_line(group_note, 180))
        if no_reply and any(word in str(question or "") for word in ("刚才", "刚刚", "没回", "不回", "没回复", "为什么")):
            lines.append("最近未回复记录：" + "；".join(no_reply))
        elif tests and any(word in str(question or "") for word in ("测试", "排障", "空间", "生图", "tts", "窥屏")):
            lines.append("最近排障测试：" + "；".join(tests))
        if checks:
            lines.append("最先看这一点：" + _single_line(checks[0], 180))
        if suggestions:
            lines.append("可以先试：" + _single_line(suggestions[0], 180))
        if proposals:
            item = proposals[0]
            key = _single_line(item.get("key"), 80)
            if key:
                lines.append(
                    f"如果要调配置，优先看 {self._companion_manual_config_ref(key, include_location=False)}，"
                    f"建议由 {self._companion_manual_format_config_item_value(key, item.get('old'))} "
                    f"改为 {self._companion_manual_format_config_item_value(key, item.get('value'))}。"
                )
        return "\n".join(line for line in lines if line)


    def _companion_manual_format_diagnostic_evidence(
        self,
        event: AstrMessageEvent,
        selected: list[dict[str, Any]],
        proposals: list[dict[str, Any]],
    ) -> str:
        lines: list[str] = []
        titles = [_single_line(item.get("title"), 50) for item in selected if isinstance(item, dict)]
        titles = [item for item in titles if item]
        if titles:
            lines.append("匹配说明书：" + " / ".join(titles[:3]))
        runtime = self._companion_manual_runtime_snapshot(event)
        runtime_lines = [
            _single_line(line, 180)
            for line in runtime.splitlines()
            if _single_line(line, 180)
        ]
        if runtime_lines:
            lines.extend(runtime_lines[:5])
        if proposals:
            config_lines = []
            for item in proposals[:6]:
                if not isinstance(item, dict):
                    continue
                key = _single_line(item.get("key"), 80)
                if key:
                    config_lines.append(
                        f"{self._companion_manual_config_ref(key, include_location=False)}="
                        f"{self._companion_manual_format_config_item_value(key, item.get('old'))}"
                    )
            if config_lines:
                lines.append("涉及可改配置：" + "、".join(config_lines))
        if not lines:
            return ""
        return "诊断依据：\n" + "\n".join(f"- {line}" for line in lines[:8])

    def _companion_manual_recent_no_reply_evidence(self, event: AstrMessageEvent | None = None, *, limit: int = 3) -> list[str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        passive = data.get("passive_no_reply_records") if isinstance(data.get("passive_no_reply_records"), dict) else {}
        items = passive.get("items") if isinstance(passive.get("items"), list) else []
        session = ""
        sender_id = ""
        if event is not None:
            session = _single_line(getattr(event, "unified_msg_origin", ""), 160)
            try:
                sender_id = _single_line(event.get_sender_id(), 80)
            except Exception:
                sender_id = ""
        ranked: list[tuple[float, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            score = _safe_float(item.get("last_ts"), 0.0, 0.0)
            if session and _single_line(item.get("last_session"), 160) == session:
                score += 10_000_000
            elif sender_id and _single_line(item.get("last_sender_id"), 80) == sender_id:
                score += 1_000_000
            reason = _single_line(item.get("reason"), 80) or "未说明原因"
            source = _single_line(item.get("source"), 30) or "被动未回复"
            inbound = _single_line(item.get("last_inbound"), 80)
            count = _safe_int(item.get("count"), 1, 1)
            text = f"{source}：{reason}×{count}"
            if inbound:
                text += f"｜最近消息：{inbound}"
            ranked.append((score, text))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _score, text in ranked[: max(1, limit)]]

    def _companion_manual_recent_test_evidence(self, *, limit: int = 3) -> list[str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        tests = data.get("troubleshooting_test_results") if isinstance(data.get("troubleshooting_test_results"), dict) else {}
        ranked: list[tuple[float, str]] = []
        for key, item in tests.items():
            if not isinstance(item, dict):
                continue
            ts = _safe_float(item.get("ran_at"), 0.0, 0.0)
            title = _single_line(item.get("title") or key, 30)
            status = "进行中" if bool(item.get("pending")) else ("通过" if bool(item.get("ok")) else "失败")
            detail = _single_line(item.get("error") or item.get("detail"), 80)
            ranked.append((ts, f"{title}：{status}{('｜' + detail) if detail else ''}"))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        return [text for _ts, text in ranked[: max(1, limit)]]

    def _companion_manual_relevant_setting_snapshot(self, selected: list[dict[str, Any]], query: str = "") -> list[str]:
        issue_tags = self._companion_manual_issue_tags(query)
        settings: list[str] = []
        for entry in selected[:2]:
            if not isinstance(entry, dict):
                continue
            for key in entry.get("settings", [])[:8]:
                key_text = str(key or "").strip()
                if not key_text or key_text in settings:
                    continue
                if key_text in self._companion_manual_config_specs():
                    current = self._companion_manual_current_config_value(key_text)
                    settings.append(
                        f"{self._companion_manual_config_label(key_text)}={self._companion_manual_format_config_item_value(key_text, current)}"
                    )
                elif key_text in self._companion_manual_config_display_meta():
                    current = self._companion_manual_current_config_value(key_text)
                    if current not in (None, ""):
                        settings.append(
                            f"{self._companion_manual_config_label(key_text)}={self._companion_manual_format_config_item_value(key_text, current)}"
                        )
        if settings:
            return settings[:8]
        fallback = []
        for item in self._companion_manual_setting_snapshot():
            if (
                ("group" in issue_tags and any(token in item for token in ("群聊", "高强度", "消息收口", "唤醒")))
                or ("rest" in issue_tags and "休息" in item)
                or ("silence" in issue_tags and "智能沉默" in item)
                or ("review" in issue_tags and "回复复核" in item)
                or ("photo" in issue_tags and "自然语言生图" in item)
                or ("state" in issue_tags and "拟人状态" in item)
                or ("style" in issue_tags and "回复风格" in item)
            ):
                fallback.append(item)
        return fallback[:5]

    def _companion_manual_can_apply_config(self, event: AstrMessageEvent) -> bool:
        is_private = bool(getattr(event, "is_private_chat", lambda: False)())
        return self._can_manage_private_companion(event) if is_private else self._can_manage_group_companion(event)

    def _companion_manual_get_pending_config(self, event: AstrMessageEvent) -> dict[str, Any] | None:
        store = self._companion_manual_pending_store()
        key = self._companion_manual_pending_key(event)
        pending = store.get(key)
        if not isinstance(pending, dict):
            return None
        if _now_ts() - _safe_float(pending.get("ts"), 0.0, 0.0) > 1800:
            store.pop(key, None)
            self._save_data_sync(sections={"manual_diagnosis_pending_config"})
            return None
        return pending

    async def _companion_manual_apply_config_value(self, key: str, value: Any) -> tuple[bool, str, Any, Any]:
        ok, normalized, error = self._companion_manual_normalize_config_value(key, value)
        if not ok:
            return False, error, None, None
        old = self._companion_manual_current_config_value(key)
        old_semantic_debounce = runtime_persona_setting(self, 'enable_semantic_message_debounce', None)
        old_semantic_seconds = runtime_persona_setting(self, 'semantic_message_debounce_seconds', None)
        setattr(self, key, normalized)
        extra_config_updates: dict[str, Any] = {}
        if key == "enable_message_debounce":
            self.enable_semantic_message_debounce = bool(normalized)
            extra_config_updates["enable_semantic_message_debounce"] = bool(normalized)
        if key == "text_message_debounce_seconds":
            self.semantic_message_debounce_seconds = normalized
            extra_config_updates["semantic_message_debounce_seconds"] = normalized
        config_value = normalized
        if key == "rest_reply_probability":
            config_value = max(0, min(100, int(round(_safe_float(normalized, 0.0, 0.0) * 100))))
        saved = False
        config = getattr(self, "config", None)
        if config is not None:
            try:
                saved = _set_into_config(config, key, config_value, allow_flat_fallback=False)
            except TypeError:
                saved = _set_into_config(config, key, config_value)
            if not saved:
                saved = _set_into_config(config, key, config_value)
            for extra_key, extra_value in extra_config_updates.items():
                try:
                    _set_into_config(config, extra_key, extra_value, allow_flat_fallback=False)
                except TypeError:
                    _set_into_config(config, extra_key, extra_value)
            if saved and not await self._save_config_if_possible():
                setattr(self, key, old)
                old_config_value = old
                if key == "rest_reply_probability":
                    old_config_value = max(0, min(100, int(round(_safe_float(old, 0.0, 0.0) * 100))))
                _set_into_config(config, key, old_config_value)
                if key == "enable_message_debounce":
                    self.enable_semantic_message_debounce = old_semantic_debounce
                    _set_into_config(config, "enable_semantic_message_debounce", old_semantic_debounce)
                if key == "text_message_debounce_seconds":
                    self.semantic_message_debounce_seconds = old_semantic_seconds
                    _set_into_config(config, "semantic_message_debounce_seconds", old_semantic_seconds)
                return False, "配置保存失败，已恢复修改前的运行配置。", old, old
        if not saved:
            logger.debug("[PrivateCompanion] 答疑设置只更新运行态,未找到可写配置项: key=%s", key)
            setattr(self, key, old)
            if key == "enable_message_debounce":
                self.enable_semantic_message_debounce = old_semantic_debounce
            if key == "text_message_debounce_seconds":
                self.semantic_message_debounce_seconds = old_semantic_seconds
            return False, "配置项无法写入，已恢复修改前的运行配置。", old, old
        return True, "", old, normalized

    async def _companion_manual_apply_pending_config(self, event: AstrMessageEvent) -> str:
        if not self._companion_manual_can_apply_config(event):
            return self._management_denied_text()
        pending = self._companion_manual_get_pending_config(event)
        if not pending:
            return "没有待确认的答疑配置建议。先用：陪伴 答疑 <问题>"
        changes = pending.get("changes") if isinstance(pending.get("changes"), list) else []
        if not changes:
            return "这次答疑没有可执行配置建议。"
        lines = ["已按刚才的答疑建议修改配置："]
        applied = 0
        for item in changes:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            ok, error, old, new = await self._companion_manual_apply_config_value(key, item.get("value"))
            if not ok:
                lines.append(f"- {key}：跳过，{error}")
                continue
            applied += 1
            lines.append(
                f"- {key}（{self._companion_manual_config_label(key)}）："
                f"由 {self._companion_manual_format_config_item_value(key, old)} 改为 {self._companion_manual_format_config_item_value(key, new)}"
                f"；{_single_line(item.get('reason'), 120) or '按答疑建议调整'}"
            )
        self._companion_manual_pending_store().pop(self._companion_manual_pending_key(event), None)
        self._save_data_sync(sections={"runtime_settings", "manual_diagnosis_pending_config"})
        if applied <= 0:
            return "没有成功应用的配置项。"
        lines.append("已保存到插件配置；如果 AstrBot 配置对象不支持同步保存，日志里会提示。")
        return "\n".join(lines)

    def _companion_manual_cancel_pending_config(self, event: AstrMessageEvent) -> str:
        store = self._companion_manual_pending_store()
        key = self._companion_manual_pending_key(event)
        existed = key in store
        store.pop(key, None)
        self._save_data_sync(sections={"manual_diagnosis_pending_config"})
        return "已取消刚才的答疑配置建议。" if existed else "当前没有待确认的答疑配置建议。"

    def _companion_manual_parse_setting_text(self, text: str) -> tuple[str, str]:
        raw = re.sub(r"^(?:把|将)\s*", "", str(text or "").strip())
        if not raw:
            return "", ""
        match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|:|：|设为|设置为|改成|调到)\s*(.+)", raw)
        if match:
            key = self._companion_manual_config_key_from_alias(match.group(1).strip())
            return key, match.group(2).strip()
        for alias, key in sorted(self._companion_manual_config_aliases().items(), key=lambda item: len(str(item[0])), reverse=True):
            alias_text = str(alias or "").strip()
            if not alias_text:
                continue
            delimiter_pattern = rf"^{re.escape(alias_text)}\s*(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*(.+)$"
            delimiter_match = re.match(delimiter_pattern, raw, flags=re.I | re.S)
            if delimiter_match:
                return key, delimiter_match.group(1).strip()
            if raw.lower().startswith(alias_text.lower()):
                tail = raw[len(alias_text):].strip()
                tail = re.sub(r"^(?:=|:|：|设为|设置为|改成|调到|调整为|调为)\s*", "", tail).strip()
                if tail:
                    return key, tail
        parts = raw.split(maxsplit=1)
        if len(parts) >= 2:
            key = self._companion_manual_config_key_from_alias(parts[0].strip())
            return key, parts[1].strip()
        return "", ""

    async def _companion_manual_apply_setting_command(self, event: AstrMessageEvent, text: str) -> str:
        if not self._companion_manual_can_apply_config(event):
            return self._management_denied_text()
        key, value = self._companion_manual_parse_setting_text(text)
        if not key or not value:
            allowed = "、".join(sorted(self._companion_manual_config_specs().keys())[:12])
            return (
                "请这样写：陪伴 答疑设置 <配置项> <值>\n"
                "例如：陪伴 答疑设置 group_high_intensity_wakeup_threshold 5\n"
                "也可以：陪伴 答疑设置 高强度阈值 5\n"
                f"可改配置很多，前几个是：{allowed} ..."
            )
        ok, error, old, new = await self._companion_manual_apply_config_value(key, value)
        if not ok:
            return error
        self._companion_manual_pending_store().pop(self._companion_manual_pending_key(event), None)
        self._save_data_sync(sections={"runtime_settings", "manual_diagnosis_pending_config"})
        if self._companion_manual_values_equal(old, new):
            return (
                "配置没有变化：\n"
                f"{key}（{self._companion_manual_config_label(key)}）本来就是 "
                f"{self._companion_manual_format_config_item_value(key, new)}"
            )
        return (
            "已修改并保存配置：\n"
            f"{key}（{self._companion_manual_config_label(key)}）："
            f"由 {self._companion_manual_format_config_item_value(key, old)} 改为 {self._companion_manual_format_config_item_value(key, new)}"
        )

    def _companion_manual_entries(self) -> list[dict[str, Any]]:
        return [
            {
                "title": "管理命令、夹层密码和权限为什么用不了",
                "keywords": ["管理员命令", "管理权限", "管理员权限", "指令失效", "命令失效", "用不了命令", "不能用命令", "夹层密码", "输出夹层密码", "强制输出", "admins_id", "target_user_ids", "UMO", "UID", "default"],
                "summary": "陪伴插件的管理命令会在执行前先检查发送者用户 ID。AstrBot 全局管理员、本插件私聊目标用户，以及私聊页中关系角色设为主要用户的用户都具有管理权限；OneBot 通常是 QQ 号，QQ 官方机器人通常是 openid/平台用户 ID。",
                "checks": [
                    "可以在 AstrBot 全局管理员配置 admins_id 中填写真实用户 ID、加入插件私聊目标用户，或在插件私聊页把该用户的关系角色改为主要用户。",
                    "OneBot/aiocqhttp 通常填 QQ 号；QQ 官方机器人请填日志或私聊页显示的 openid/平台用户 ID。",
                    "target_user_ids 一行一个或用逗号分隔；优先直接填写用户 ID，误粘贴私聊 UMO 时会尝试提取 FriendMessage 后面的用户 ID。",
                    "不要放 default、aiocqhttp、UID、平台名或群聊会话串；这些不是私聊发送者用户 ID。",
                    "群管理员只用于群聊管理命令，不会自动获得私聊里的夹层密码、重置插件、生成日程等私聊管理权限。",
                    "夹层密码相关命令属于管理命令：陪伴 输出夹层密码、陪伴 强制输出 夹层密码、陪伴 重置夹层密码。",
                    "如果用户只是自然语言问“夹层密码是什么”，不会直接走管理输出；需要使用明确命令且通过权限检查。",
                ],
                "settings": [
                    "target_user_ids",
                ],
                "suggestions": [
                    "先让用户发：陪伴 状态，确认命令能被插件接管；如果提示需要管理权限，就检查 admins_id、私聊目标用户 ID或该用户的关系角色。",
                    "如果日志里看到 UID/default，说明查的是会话定位，不是权限 ID；权限配置要回到 event.get_sender_id() 对应的稳定用户 ID。",
                ],
            },
            {
                "title": "刚才被动消息为什么没回",
                "keywords": ["刚才没回", "刚才没回复", "刚刚没回", "为什么没回", "为什么不回", "没有回复", "被动未回复", "空结果", "跳过发送", "消息链全为空", "没发出来"],
                "summary": "这类问题先看最近被动未回复记录，而不是先猜人格。常见来源包括休息回复闸门、智能沉默、回复复核拦截、群聊答疑碰瓷复核、空结果兜底、自然语言生图或其他命令接管。",
                "checks": [
                    "排障页和答疑运行态都会合并最近被动未回复原因，重复原因不会刷屏。",
                    "如果来源是休息回复闸门，重点看 enable_rest_reply_simulation、rest_reply_mode 和 rest_reply_awake_grace_minutes。",
                    "如果来源是智能沉默，重点看 enable_smart_silence 和 smart_silence_min_confidence。",
                    "如果来源是群聊答疑复核，说明发送前判断这次回复像碰瓷插话。",
                    "如果来源是空结果/消息链全为空，要继续查哪个插件或工具阶段清空了结果。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_awake_grace_minutes",
                    "enable_smart_silence",
                    "smart_silence_min_confidence",
                    "enable_group_wakeup_question",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "先问“陪伴 答疑 刚才为什么没回复”，让它带出最近未回复记录。",
                    "如果最近记录为空，再看 AstrBot 日志里 respond.stage、on_decorating_result 和插件命令是否提前 stop_event。",
                ],
            },
            {
                "title": "群聊老是不回复或好久才回复",
                "keywords": ["群聊", "群内", "群里", "不活跃", "活跃", "不回复", "没回复", "好久", "回复慢", "没反应", "不理", "卡住", "延迟"],
                "summary": "群聊回复不是所有消息都接管，通常要被 @、引用、命中唤醒、或处在连续对话窗口内。慢回复多半来自收口等待、高强度合并、模型超时或主链排队。",
                "checks": [
                    "先确认目标群启用了群聊陪伴，并且白名单/黑名单没有挡住。",
                    "如果没有 @/引用 Bot，只有“群聊连续对话保持”窗口内的同一用户后续发言才可能续接。",
                    "如果短时间连续叫 Bot，高强度收口会合并多条消息后再回复，看起来会慢几秒。",
                    "如果开启智能文本收口，短引子、逗号结尾、疑似没说完的话会先等补话。",
                    "如果日志里有 AstrBot 主链排队、Provider timeout、休息回复闸门或智能沉默，回复也可能被延后或取消。",
                ],
                "settings": [
                    "enable_group_companion",
                    "group_access_mode",
                    "enable_group_conversation_followup",
                    "enable_group_high_intensity_mode",
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "想更容易接话：打开群聊连续对话，并把窗口设为 90-180 秒。",
                    "想少等：降低文本/短唤醒等待，或关闭智能文本收口。",
                    "高强度群里想减少误压制：阈值调到 4-5，持续时间调到 60-90 秒，合并范围改 same_user。",
                ],
            },
            {
                "title": "群聊连续对话在哪里设置",
                "keywords": ["连续对话", "续接", "上下文续接", "followup", "接话", "没at", "没@", "设置在哪"],
                "summary": "配置项是 enable_group_conversation_followup。开启后，群里同一用户明确 @/引用 Bot 之后，短时间内没继续 @ 的后续消息也会判断是否仍在和 Bot 对话。",
                "checks": [
                    "设置入口：配置页搜索“群聊连续对话保持”或 enable_group_conversation_followup。",
                    "窗口：group_conversation_followup_seconds，决定多久内还能续接。",
                    "轮数：group_conversation_followup_max_turns，决定不继续 @ 时最多自动续几轮。",
                    "模型：GROUP_FOLLOWUP_JUDGE_PROVIDER_ID 只在规则不确定时使用；留空时会先跟随快速响应模型，快速响应模型也留空时只走规则判断。",
                ],
                "settings": [
                    "enable_group_conversation_followup",
                    "group_conversation_followup_seconds",
                    "group_conversation_followup_max_turns",
                    "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "推荐：窗口 120 秒，最多 1-2 轮，小模型可填低延迟分类模型。",
                    "如果经常碰瓷回复，把最大轮数设为 1，并填写续接判断模型。",
                ],
            },
            {
                "title": "群聊高强度收口和连续对话的关系",
                "keywords": ["高强度", "收口", "合并", "冲突", "连续对话", "压制", "高强度收口"],
                "summary": "两者不是硬冲突，但高强度收口优先级更高。近期连续叫到 Bot 时会合并明确消息，并暂停不确定的续接模型判断；冷却残留只降载，不再延迟单条明确 @。",
                "checks": [
                    "触发条件：group_high_intensity_wakeup_window_seconds 内唤醒次数达到 group_high_intensity_wakeup_threshold；唤醒疲劳只会在近期已有连续唤醒时辅助触发。",
                    "持续时间：group_high_intensity_cooldown_seconds。",
                    "合并范围：group 表示全群叫 Bot 合并；same_user 表示只合并同一用户补话。",
                    "高强度冷却期间明确 @/引用仍会处理；只有近期仍在连续叫 Bot 时才进入合并等待。",
                ],
                "settings": [
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_wakeup_window_seconds",
                    "group_high_intensity_wakeup_threshold",
                    "group_high_intensity_cooldown_seconds",
                    "group_high_intensity_merge_seconds",
                    "group_high_intensity_merge_scope",
                ],
                "suggestions": [
                    "想保留对话感：阈值 4-5、持续 60-90 秒、合并范围 same_user。",
                    "想极限省 token：保持默认 group 合并，并接受高强度期间续接变保守。",
                ],
            },
            {
                "title": "消息收口/智能防抖是什么",
                "keywords": ["防抖", "收口", "智能收口", "补话", "等补充", "等待", "合并消息"],
                "summary": "消息收口会给用户留一点补充时间，把连续几句话合并成同一轮；智能收口会先判断这句话是不是完整，只有像“问你个事/你猜/等等/逗号结尾”才等待。",
                "checks": [
                    "总开关：enable_message_debounce。",
                    "智能文本收口：enable_smart_message_debounce。",
                    "固定文本等待：text_message_debounce_seconds；智能收口开启时主要看 smart_message_debounce_wait_seconds。",
                    "最长等待：text_message_debounce_max_wait_seconds，避免一直补话拖住。",
                    "最大合并：message_debounce_max_merge_messages，达到后立刻进入回复链。",
                ],
                "settings": [
                    "enable_message_debounce",
                    "enable_smart_message_debounce",
                    "text_message_debounce_seconds",
                    "smart_message_debounce_wait_seconds",
                    "text_message_debounce_max_wait_seconds",
                    "message_debounce_max_merge_messages",
                ],
                "suggestions": [
                    "觉得慢：文本等待设 0-2 秒，智能等待设 1-2 秒。",
                    "用户常先发图/转发再补字：图片/转发等待可以保留 5-8 秒。",
                ],
            },
            {
                "title": "群聊唤醒、@ 和答疑误触",
                "keywords": ["唤醒", "@", "at", "艾特", "答疑", "误触", "碰瓷", "为什么插话"],
                "summary": "群聊默认不会每句话都回复。明确 @/引用最强；名字、弱唤醒词、兴趣词、公共求助问题会按规则和概率进入回复链。答疑类回复发送前还有碰瓷复核。",
                "checks": [
                    "强触发：@ Bot、引用 Bot、直接叫 Bot 名字。",
                    "弱触发：group_wakeup_context_words、group_wakeup_interest_keywords、公共求助问题。",
                    "公共求助由 enable_group_wakeup_question 和 group_wakeup_question_threshold 控制。",
                    "答疑误触可看日志里的“群聊答疑回复发送前复核”。",
                ],
                "settings": [
                    "enable_group_wakeup_enhancement",
                    "group_wakeup_direct_words",
                    "group_wakeup_owner_direct_words",
                    "group_wakeup_context_words",
                    "group_wakeup_interest_keywords",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：提高求助阈值，删掉泛化弱唤醒词，保留 Bot 名字和明确 @。",
                    "不回复多：检查是否被冷却/疲劳/高强度压制。",
                ],
            },
            {
                "title": "休息回复闸门和晚安后不回",
                "keywords": ["休息闸门", "休息回复", "睡眠闸门", "睡眠回复", "晚安", "睡觉", "睡眠", "不回消息", "说句晚安", "醒后补看"],
                "summary": "休息回复闸门只在 Bot 当前日程像睡眠/午休且处于配置窗口时生效。它会在回复链前判断要不要醒来；被拦截的私聊会记录到被动未回复，并可在醒后补看。",
                "checks": [
                    "总开关：enable_rest_reply_simulation；关闭后不会因为睡眠状态拦截被动回复。",
                    "模式：rest_reply_mode。probability 是概率醒来，llm 是小模型判断是否需要醒来。",
                    "模型阈值：rest_reply_llm_threshold，越高越不容易醒来。",
                    "清醒宽限：rest_reply_awake_grace_minutes，被叫醒后这段时间内不容易再次被当成休息中。",
                    "醒后补看：enable_rest_backlog_reply 和 rest_backlog_max_messages 控制被挡住的私聊是否下次注入。",
                ],
                "settings": [
                    "enable_rest_reply_simulation",
                    "rest_reply_mode",
                    "rest_reply_probability",
                    "rest_reply_llm_threshold",
                    "rest_reply_awake_grace_minutes",
                    "enable_rest_backlog_reply",
                    "rest_backlog_max_messages",
                    "REST_WAKEUP_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：先关闭 enable_rest_reply_simulation 止血，或改成 llm 模式并提高醒来判断质量。",
                    "只是晚安后不想整段断联：把清醒宽限调到 45-60 分钟。",
                    "想保留拟人睡眠：打开醒后补看，避免 token 已省但用户消息彻底丢上下文。",
                ],
            },
            {
                "title": "繁忙回复闸门和主动顺延",
                "keywords": ["繁忙闸门", "繁忙回复", "忙碌回复", "回复太快", "忙时延迟", "忙完再发", "主动顺延"],
                "summary": "繁忙回复闸门默认关闭。开启后只根据 Bot 当前日程和细化状态判断忙碌，不增加模型调用；普通被动消息会延迟但不会静默，普通主动消息会顺延到忙完并经过缓冲。",
                "checks": [
                    "总开关：enable_busy_reply_gate；关闭时被动和主动链路都保持原节奏。",
                    "私聊延迟：busy_reply_min_delay_seconds 到 busy_reply_max_delay_seconds；群聊会自动把最长等待限制到 12 秒。",
                    "紧急、安全风险和插件管理命令立即放行，不等待繁忙延迟。",
                    "主动缓冲：busy_reply_proactive_resume_buffer_minutes；普通主动候选顺延到当前忙碌片段结束后再等待这段时间。",
                    "用户预约、到期便签、环境突变、排障和模拟消息不参与主动顺延。",
                ],
                "settings": [
                    "enable_busy_reply_gate",
                    "busy_reply_min_delay_seconds",
                    "busy_reply_max_delay_seconds",
                    "busy_reply_proactive_resume_buffer_minutes",
                ],
                "suggestions": [
                    "默认私聊会等待 1-5 分钟；想让忙碌感更明显，可继续提高，但单项最多 15 分钟。",
                    "群聊无需单独配置；页面设置再大也会自动限制在 12 秒内。",
                ],
            },
            {
                "title": "智能沉默和结束话题",
                "keywords": ["智能沉默", "智能静默", "沉默", "静默", "不继续话题", "结束话题", "别回", "别说话", "不想聊", "停止回复"],
                "summary": "智能沉默是在回复发送前工作的。主模型先生成回复，小模型再判断这轮是否应该安静收住；默认只看明确边界，也可以切到上下文模型判断。",
                "checks": [
                    "总开关：enable_smart_silence。",
                    "判断模式：smart_silence_judge_mode，boundary_only 只看明确边界，contextual 会把短句收尾、忙了/睡了、敷衍回应等也交给模型判断。",
                    "阈值：smart_silence_min_confidence，越高越不容易沉默。",
                    "模型：SMART_SILENCE_PROVIDER_ID；留空时会走插件模型回退。",
                    "群聊场景会参考最近真实群聊上下文，不只看唤醒消息。",
                    "如果已经发出去了，就不是智能沉默拦截；智能沉默的发送前兜底只会取消待发送回复。",
                ],
                "settings": [
                    "enable_smart_silence",
                    "smart_silence_judge_mode",
                    "smart_silence_min_confidence",
                    "smart_silence_model_timeout_seconds",
                    "SMART_SILENCE_PROVIDER_ID",
                ],
                "suggestions": [
                    "误触多：把置信度调到 0.76-0.82，仍误触再临时关闭。",
                    "希望更准确：给 SMART_SILENCE_PROVIDER_ID 填低延迟但能稳定输出 JSON 的小模型。",
                ],
            },
            {
                "title": "回复复核/去重为什么会拦截",
                "keywords": ["回复复核", "主动复核", "复核", "去重", "复读", "重复回复", "误杀", "截断", "被拦截", "为什么被拦截"],
                "summary": "被动回复复核主要防止复读、串台、工具回执和异常文本外发；它与主动消息终审已经独立开关。",
                "checks": [
                    "先看最近被动未回复记录里 source 是“回复复核去重”“发送前拦截”还是“群聊答疑复核”。",
                    "passive_review_mode=full 时，普通被动回复也更容易进入模型改写；severe_only 更像默认保护层。",
                    "response_review_max_chars 太低时，短闲聊也可能被当成需要复核的长回复。",
                    "群聊答疑碰瓷复核不等同于普通去重，它只处理公共求助/答疑唤醒产生的可疑插话。",
                    "如果拦的是“消息已发送/发送成功”这类回执，通常是工具链回执被保护性拦截，不该关掉复核。",
                ],
                "settings": [
                    "enable_passive_response_review",
                    "passive_review_mode",
                    "passive_review_strength",
                    "response_review_max_chars",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "enable_group_wakeup_question",
                    "group_wakeup_question_threshold",
                ],
                "suggestions": [
                    "误杀普通回复：先使用 passive_review_strength=lenient；仍有问题可关闭 enable_passive_response_review，主动终审不会受影响。",
                    "只是群里答疑碰瓷被拦：优先调群聊解惑阈值，不要直接关整个复核。",
                ],
            },
            {
                "title": "回复太长或不听人格字数限制",
                "keywords": ["话多", "太长", "回复太长", "一堆话", "15字", "十五字", "简洁", "口语化", "回复风格", "人设限制", "人格限制"],
                "summary": "人格里的字数限制仍然有效，但群聊高强度、关系网、动态状态、记忆、图片和转发上下文注入太多时，模型可能更想解释完整而变长。回复风格约束会作为更靠近当前请求的表达节奏提示注入。",
                "checks": [
                    "回复风格配置：reply_style_prompt，会进入普通聊天和主动消息的请求动态块。",
                    "它不是只作用于私聊；群聊主链、主动和被动都会尽量遵守，但复杂排障/教程可例外。",
                    "如果群聊只想极短，可以把句数、语言、口语化、复杂说明例外写清楚。",
                    "如果仍然超长，要看是否有其他插件或主人格在系统提示词里要求详细解释。",
                ],
                "settings": [
                    "reply_style_prompt",
                    "enable_group_high_intensity_mode",
                    "group_high_intensity_max_merge_messages",
                    "enable_message_debounce",
                ],
                "suggestions": [
                    "推荐写法：每次回复至多三句话；简单回答尽量 1-2 句；口语化、简洁；复杂问题或用户要求详细时例外。",
                    "群聊动态注入过多时，也可以降低高强度合并条数，减少一次性喂给模型的信息量。",
                ],
            },
            {
                "title": "模型和 Provider 配置",
                "keywords": ["llm", "LLM", "模型", "provider", "Provider", "配置模型", "没配置", "子模型", "小模型", "默认模型", "超时", "timeout", "降级", "无有效json"],
                "summary": "插件多数能力默认跟随 AstrBot 当前会话模型；部分功能可以单独指定 Provider。未单独配置时通常不是没模型，而是会回退到主模型或相关默认模型；模型超时或 JSON 无效时，部分判定会降级本地规则。",
                "checks": [
                    "主聊天回复通常使用 AstrBot 当前会话选择的人格和 Provider。",
                    "可以先用快速配置只填 4 类：快速响应模型、复杂推理模型、创作模型、插件视觉模型；高级单项留空时会自动套用这些快速配置。",
                    "陪伴答疑优先使用 TROUBLESHOOTING_PROVIDER_ID；快速配置下默认使用复杂推理模型，精准配置未填时优先回退到复杂推理/插件主模型。",
                    "智能收口使用 SMART_MESSAGE_DEBOUNCE_PROVIDER_ID；留空时跟随插件主模型。",
                    "生图模型不等于聊天模型，需要在生图平台/后端配置里单独确认。",
                    "日志出现 Request timed out、无有效 JSON、降级本地判定时，说明这次不是人格问题，而是某个小模型/主模型没在预算内稳定返回。",
                    "如果提示 Provider 不可用、模型超时或空回复，再看对应功能的 Provider ID 和 Token 页错误。",
                ],
                "settings": [
                    "LLM_PROVIDER_ID",
                    "FAST_RESPONSE_PROVIDER_ID",
                    "COMPLEX_REASONING_PROVIDER_ID",
                    "CREATIVE_MODEL_PROVIDER_ID",
                    "TROUBLESHOOTING_PROVIDER_ID",
                    "RESPONSE_REVIEW_PROVIDER_ID",
                    "MAI_STYLE_PROVIDER_ID",
                    "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
                    "SMART_SILENCE_PROVIDER_ID",
                    "PHOTO_MODEL_PROVIDER_ID",
                    "PHOTO_PROMPT_PROVIDER_ID",
                ],
                "suggestions": [
                    "只想先跑通：保留子模型为空，让它跟随主模型。",
                    "想降低延迟：答疑、智能收口、复核类功能可填低延迟小模型。",
                    "生图失败时优先检查生图平台和图片模型，不要填普通聊天模型。",
                ],
            },
            {
                "title": "“我会牢牢记住你”联动和未安装兼容",
                "keywords": ["rememberyou", "remember you", "我会牢牢记住你", "记忆插件", "专属记忆", "知识图谱", "未安装", "联动", "记忆查询"],
                "summary": "“我会牢牢记住你”是可选深度联动，不是陪伴插件硬依赖。安装后，陪伴插件会把日程、穿搭、创作、主动消息、用户习惯等结构化反馈给记忆插件；未安装时，本地关系网、状态和短期上下文仍照常工作。",
                "checks": [
                    "未安装“我会牢牢记住你”时，答疑、群聊、主动、状态模拟不会因此直接失效，只是少了长期结构化检索和图谱补充。",
                    "安装后，答疑会尝试读取近期排障/配置记忆，但本地运行状态、截图和日志仍优先。",
                    "私聊和群聊记忆边界仍由陪伴插件控制，不会把私聊隐私直接带进群聊。",
                    "如果联动导致超时，应该看到“我会牢牢记住你”读取失败或超时日志；陪伴插件会回退本地上下文。",
                ],
                "settings": [
                    "enable_companion_memory",
                    "enable_group_episode_memory",
                    "enable_group_privacy_guard",
                ],
                "suggestions": [
                    "想验证联动：先问穿搭、进食、最近主动消息这类明确事实，再看“我会牢牢记住你”是否有对应个人记忆。",
                    "担心 CPU 或超时：保持“我会牢牢记住你”为软依赖，不要把它放进每轮必须成功的阻塞链路。",
                ],
            },
            {
                "title": "主动消息不发或很少发",
                "keywords": ["主动", "不主动", "不发消息", "很久没发", "主动消息", "私聊主动"],
                "summary": "主动消息会受目标用户、每日上限、最小间隔、免打扰、休息状态、用户很久没回、主动价值复核和发送失败重试影响。",
                "checks": [
                    "确认用户 ID 已加入插件私聊目标用户，或已在私聊页启用。",
                    "检查 max_daily_messages、min_interval_minutes、quiet_hours。",
                    "如果用户长期不回，主动会变短、变少，甚至延后。",
                    "如果开启主动消息价值复核，低价值或像打扰的消息会被改写/拦截。",
                ],
                "settings": [
                    "target_user_ids",
                    "max_daily_messages",
                    "min_interval_minutes",
                    "quiet_hours",
                    "proactive_review_strength",
                    "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
                ],
                "suggestions": [
                    "先用“陪伴 查看主动判定”和扩展页排障看最近一次跳过原因。",
                    "调试期把每日上限设 2-5，间隔不要太短，更容易观察真实行为。",
                ],
            },
            {
                "title": "拟人身体状态、饥饿和生理期",
                "keywords": ["饥饿", "一直饿", "一天都是饥饿", "胃口", "健康状态", "不适状态", "生理期", "姨妈", "来月经", "情绪太低", "情绪过低", "拟人状态"],
                "summary": "拟人身体状态来自日程、时间段、状态强度和条件状态。当前逻辑里，生理期模拟开关开启就视为适用；关闭后会清理旧的生理期条件。饥饿状态如果长时间不退，多半是强度偏高或进食记录没有被日程/记忆及时更新。",
                "checks": [
                    "健康状态：enable_health_state。",
                    "饥饿/胃口：enable_hunger_state。",
                    "生理期：enable_cycle_state，开启就认为适用模拟。",
                    "状态强度：humanized_state_intensity，越高越容易把状态写进提示词和主动候选。",
                    "如果接入“我会牢牢记住你”，进食、穿搭、习惯等会尽量以可检索的个人记忆补充，但未安装时插件仍能用本地状态运行。",
                ],
                "settings": [
                    "enable_health_state",
                    "enable_hunger_state",
                    "enable_cycle_state",
                    "humanized_state_intensity",
                ],
                "suggestions": [
                    "一天都饿：先关 enable_hunger_state 或把 humanized_state_intensity 降到 30-40。",
                    "不想要生理期模拟：直接关闭 enable_cycle_state。",
                    "情绪/身体状态压过人格：降低状态强度，不要只改人格。"
                ],
            },
            {
                "title": "生图/自拍/参考图问题",
                "keywords": ["生图", "自拍", "参考图", "图片", "画图", "改图", "不出图", "脸", "分辨率", "没反应", "好了", "穿搭图", "提示词"],
                "summary": "生图链路会优先使用配置的在线 API，失败后按配置回退；参考图一致性是可选子功能，开启后才会自动使用人设/穿搭参考图。非指令生图默认走工具优先，由主链模型调用 pc_generate_photo；规则快判只适合需要插件前置抢接单的场景。",
                "checks": [
                    "确认 enable_photo_text_action 和生图后端配置可用。",
                    "自拍/头像/角色表情包需要自动套人设或穿搭参考图时，先开启 enable_photo_reference_image；关闭时只按提示词生成。",
                    "非指令生图模式：natural_language_photo_generation_mode，可选 tool_first / rule_fast / off。",
                    "规则快判前置接管需要 enable_natural_language_photo_generation=true；显式指令和 pc_generate_photo 工具不依赖这个开关。",
                    "会话范围额度：photo_generation_private_owner_max_daily、photo_generation_private_friend_max_daily、photo_generation_group_max_daily、photo_generation_proactive_max_daily；每项均为 -1 不限量、0 不允许、正数为每日限额。",
                    "用户请求上限：command_photo_generation_max_daily，同时作用于显式陪伴生图指令和 pc_generate_photo 工具；-1 表示不限量，0 表示不允许，正数表示每日限额。",
                    "参考图命令：陪伴 参考图 <本地图片路径|图片URL|清空|查看>，也可带图或回复图片；查看会把当前实际参考图发出来检查。",
                    "多参考图库：发送一张或多张图片并使用“陪伴 参考图库 添加 <用途注释>”；支持列表、预览、删除和清空。用途注释写清服装、地点和适用场景，生图时会结合最终画面自动选一张，今日穿搭图不会无条件优先。",
                    "规则快判上限：natural_language_photo_generation_max_daily，只作用于 rule_fast。",
                    "分类型固定提示词：photo_generation_text2img_fixed_prompt、photo_generation_selfie_fixed_prompt、photo_generation_edit_fixed_prompt；分别作用于文生图、自拍/人像和改图，并经过清洗后写入生图日志。",
                    "兼容全局固定提示词：photo_generation_fixed_prompt 非空时仍叠加到所有类型；完全按类型控制时请留空。",
                    "提示词表达方式：photo_generation_prompt_format，可选 traditional（传统标签/短语）、natural_language（自然语言描述）或 nai（NAI 联动模式，按 NovelAI 4/4.5 标签语法书写并原样提交），全局作用于实际生图提示词。",
                    "Agnes Image 可选 platform=agnes，推荐 agnes-image-2.1-flash；参考图走 generations + extra_body.image，队列项可配置 1K-4K 与 ratio。",
                    "OpenRouter 可选 platform=openrouter；填写 https://openrouter.ai/api/v1，文生图走 /images/generations，参考图走 JSON /images + input_references。",
                    "MiniMax 可选 platform=minimax，国内站使用 https://api.minimaxi.com/v1/image_generation，模型填写 image-01 或 image-01-live；参考图走同一 JSON 接口的 subject_reference。",
                    "排障页可看最近生图提示词、参考图数量、后端错误和任务状态。",
                ],
                "settings": [
                    "enable_photo_text_action",
                    "natural_language_photo_generation_mode",
                    "photo_generation_private_owner_max_daily",
                    "photo_generation_private_friend_max_daily",
                    "photo_generation_group_max_daily",
                    "photo_generation_proactive_max_daily",
                    "command_photo_generation_max_daily",
                    "enable_natural_language_photo_generation",
                    "natural_language_photo_generation_max_daily",
                    "natural_language_photo_extra_prompt",
                    "enable_photo_reference_image",
                    "photo_reference_catalog",
                    "photo_generation_backend",
                    "photo_generation_prompt_format",
                    "photo_generation_fixed_prompt",
                    "photo_generation_text2img_fixed_prompt",
                    "photo_generation_selfie_fixed_prompt",
                    "photo_generation_edit_fixed_prompt",
                    "photo_generation_scene_presets",
                ],
                "suggestions": [
                    "如果误触多，先把 natural_language_photo_generation_mode 调回 tool_first，必要时改 off。",
                    "如果只有某类会话被禁止或额度用完，先检查对应的四项会话范围额度；不要用总额度代替范围控制。",
                    "如果只有用户明确请求提示额度用完或被禁止，检查 command_photo_generation_max_daily；设为 -1 即不限量，0 表示不允许，不要修改主动带图或规则快判上限。",
                    "如果没反应，先确认 enable_photo_text_action、生图后端、主链工具是否注册；只有 rule_fast 才看规则快判开关和每日上限。",
                    "如果出图后只回“好了”，重点看图片任务回调和结果说明模板，而不是聊天主模型。",
                    "在线 API 报模型错误时，确认图片模型不是普通聊天模型。",
                ],
            },
            {
                "title": "QQ 空间评论或说说链路",
                "keywords": ["qq空间", "空间", "说说", "评论", "回复评论", "一直回复", "onebot", "cookie", "点赞", "首次使用", "第一次使用", "登录空间"],
                "summary": "QQ 空间功能依赖 OneBot/Cookie 能力，评论收件箱默认应谨慎开启，并记录已见评论 ID 防止重复回复。",
                "checks": [
                    "QQ 空间属于陪伴本体保留的 OneBot 兼容能力，不依赖 astrbot_plugin_content_companion；先确认当前实例检测到可用 OneBot/aiocqhttp，再确认 enable_qzone_integration 和对应子功能开启。",
                    "第一次使用时，先在 OneBot/适配器所在账号完成 QQ 空间登录；排障提示“请先登录空间”通常表示 Cookie 不可用或已失效。QQ 官方机器人不会显示可用的 QQ 空间入口。",
                    "如果日志提示没有可用 OneBot 连接，通常是当前适配器没有暴露可取 Cookie 的连接。",
                    "点赞失效时优先跑排障页 QQ 空间测试，看 Cookie、g_tk、说说列表和点赞接口哪一步失败。",
                    "重复回复同一评论时，重点看 comment_inbox_seen_ids / replied_ids 是否保存。",
                    "可在排障页跑 QQ 空间测试。",
                ],
                "settings": [
                    "enable_qzone_integration",
                    "enable_qzone_life_publish",
                    "enable_qzone_comment_inbox",
                    "qzone_comment_inbox_interval_minutes",
                    "qzone_comment_inbox_recent_posts",
                    "qzone_comment_inbox_max_replies_per_tick",
                ],
                "suggestions": [
                    "评论回复建议低概率、长间隔、默认关闭，先用测试链路确认不会重复回复。",
                ],
            },
        ]

    def _companion_manual_select_entries(self, query: str) -> list[dict[str, Any]]:
        compact = re.sub(r"\s+", "", query).lower()
        issue_tags = self._companion_manual_issue_tags(query)
        entries = self._companion_manual_entries()
        scored: list[tuple[int, dict[str, Any]]] = []
        for entry in entries:
            score = 0
            entry_tags = self._companion_manual_entry_tags(entry)
            for keyword in entry.get("keywords", []):
                key = re.sub(r"\s+", "", str(keyword or "")).lower()
                if key and key in compact:
                    score += max(2, min(8, len(key)))
            title = re.sub(r"\s+", "", str(entry.get("title") or "")).lower()
            if title and any(part and part in compact for part in re.split(r"[、/ ]+", title)):
                score += 2
            overlap = issue_tags & entry_tags
            if overlap:
                score += 6 * len(overlap)
            if "recent" in issue_tags and entry_tags & {"rest", "silence", "debounce", "group", "photo"}:
                score += 2
            if "location" in issue_tags and any(str(item or "").strip() for item in entry.get("settings", [])):
                score += 1
            if issue_tags and entry_tags and not overlap:
                score -= 3
            if "photo" in issue_tags and entry_tags & {"group", "debounce"} and "group" not in issue_tags:
                score -= 8
            if "silence" in issue_tags and entry_tags == {"group"}:
                score -= 8
            if "rest" in issue_tags and entry_tags & {"group", "photo"}:
                score -= 6
            if score > 0:
                scored.append((score, entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in scored[:3]]

    def _companion_manual_context_text(self, selected: list[dict[str, Any]] | None = None) -> str:
        entries = self._companion_manual_entries()
        selected_titles = {
            _single_line(item.get("title"), 80)
            for item in (selected or [])
            if isinstance(item, dict) and _single_line(item.get("title"), 80)
        }
        detailed_entries = [item for item in (selected or []) if isinstance(item, dict)][:4]
        blocks: list[str] = []
        for entry in detailed_entries:
            title = _single_line(entry.get("title"), 80)
            if not title:
                continue
            checks = "；".join(str(item) for item in entry.get("checks", [])[:6] if str(item or "").strip())
            suggestions = "；".join(str(item) for item in entry.get("suggestions", [])[:4] if str(item or "").strip())
            settings = "；".join(
                self._companion_manual_config_ref(str(item), include_location=True)
                for item in entry.get("settings", [])[:12]
                if str(item or "").strip()
            )
            blocks.append(
                "\n".join(
                    [
                        f"【{title}】",
                        f"逻辑：{entry.get('summary') or ''}",
                        f"检查：{checks or '无'}",
                        f"建议：{suggestions or '无'}",
                        f"配置键：{settings or '无'}",
                    ]
                )
            )
        index_lines: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            title = _single_line(entry.get("title"), 80)
            if not title or title in selected_titles:
                continue
            summary = _single_line(entry.get("summary"), 220)
            settings = ", ".join(
                _single_line(item, 80)
                for item in (entry.get("settings") if isinstance(entry.get("settings"), list) else [])[:8]
                if _single_line(item, 80)
            )
            index_lines.append(f"- {title}：{summary or '见插件实现'}；相关配置：{settings or '无'}")
        if index_lines:
            blocks.append("【其他能力索引（用于发现相关链路，不是预设答案）】\n" + "\n".join(index_lines))
        return "\n\n".join(blocks)[:18000]

    @staticmethod
    def _companion_manual_source_file_names() -> tuple[str, ...]:
        return (
            "README.md",
            "CHANGELOG.md",
            "_conf_schema.json",
            "constants.py",
            "main.py",
            "command_handlers.py",
            "proactive.py",
            "proactive_engine.py",
            "proactive_message.py",
            "daily_state.py",
            "event_dispatch.py",
            "llm_tool_actions.py",
            "creative.py",
            "page_api.py",
            "user_memory.py",
            "private_image.py",
            "group_wakeup.py",
            "group_observation.py",
            "qzone_integration.py",
            "tts_enhancement.py",
            "token_budget.py",
        )

    def _companion_manual_source_context(
        self,
        question: str,
        selected: list[dict[str, Any]] | None = None,
        *,
        max_chars: int = 12000,
    ) -> str:
        """Retrieve small UTF-8 source excerpts so the model can answer beyond hard-coded FAQ entries."""
        query = str(question or "").strip()
        terms: list[str] = []

        def add_term(value: Any) -> None:
            term = _single_line(value, 100).strip().lower()
            if len(term) >= 2 and term not in terms:
                terms.append(term)

        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,12}", query):
            add_term(token)
        for entry in (selected or [])[:4]:
            if not isinstance(entry, dict):
                continue
            add_term(entry.get("title"))
            for keyword in (entry.get("keywords") if isinstance(entry.get("keywords"), list) else [])[:16]:
                keyword_text = _single_line(keyword, 40)
                if keyword_text and (keyword_text.lower() in query.lower() or len(keyword_text) <= 5):
                    add_term(keyword_text)
            for key in (entry.get("settings") if isinstance(entry.get("settings"), list) else [])[:12]:
                add_term(key)
        for key in self._companion_manual_mentioned_config_keys(query):
            add_term(key)
        if not terms:
            return ""

        root = Path(__file__).resolve().parent
        candidates: list[tuple[int, str, int, list[str]]] = []
        for file_name in self._companion_manual_source_file_names():
            path = root / file_name
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeError):
                continue
            scored_lines: list[tuple[int, int]] = []
            for index, line in enumerate(lines):
                lowered = line.lower()
                score = sum((8 if "_" in term else min(5, len(term))) for term in terms if term in lowered)
                if score > 0:
                    scored_lines.append((score, index))
            scored_lines.sort(key=lambda item: (-item[0], item[1]))
            used_ranges: list[tuple[int, int]] = []
            for score, index in scored_lines[:12]:
                start = max(0, index - 3)
                end = min(len(lines), index + 5)
                if any(not (end <= old_start or start >= old_end) for old_start, old_end in used_ranges):
                    continue
                used_ranges.append((start, end))
                excerpt = lines[start:end]
                candidates.append((score, file_name, start + 1, excerpt))
                if len(used_ranges) >= 3:
                    break
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        blocks: list[str] = []
        length = 0
        for _score, file_name, start_line, excerpt in candidates[:18]:
            block = f"【{file_name}:{start_line}】\n" + "\n".join(excerpt)
            if length + len(block) > max_chars:
                remaining = max_chars - length
                if remaining > 240:
                    blocks.append(block[:remaining])
                break
            blocks.append(block)
            length += len(block) + 2
        return "\n\n".join(blocks)

    def _companion_manual_local_answer(self, event: AstrMessageEvent, question: str) -> tuple[str, list[dict[str, Any]]]:
        query = _single_line(question, 260)
        if not query:
            return (
                "你直接说刚才发生了什么就行，比如：\n"
                "- 刚才群里为什么没回我\n"
                "- 发图后为什么等了几秒\n"
                "- 主动消息为什么今天没发\n"
                "- 这个开关开了到底有没有生效\n"
                "最好带上发生的场景、时间和一张截图；我会先查最可能的一条链路。"
            ), []
        selected = self._companion_manual_select_entries(query)
        if not selected:
            return (
                "我还没法把它定位到一条具体链路。\n"
                "把现象补成“在哪里发生 + Bot 做了什么/没做什么 + 大概什么时候”，我就能按运行态查。\n"
                "例如：群里 @ 了 Bot 但没回、私聊发图后等了 5 秒、刚才主动消息没发出来。"
            ), []
        primary = selected[0] if isinstance(selected[0], dict) else {}
        title = _single_line(primary.get("title"), 60) or "这条功能链路"
        summary = _single_line(primary.get("summary"), 180)
        issue_tags = self._companion_manual_issue_tags(query)
        recent_words = ("刚才", "刚刚", "今天", "最近", "没回", "不回", "没回复", "为什么", "失败", "报错")
        recent_requested = bool(issue_tags & {"recent", "error", "photo", "qzone"}) or any(word in query for word in recent_words)
        evidence: list[str] = []
        if recent_requested:
            evidence.extend(self._companion_manual_recent_no_reply_evidence(event, limit=1))
        if issue_tags & {"photo", "qzone"} or any(word in query for word in ("测试", "排障", "报错", "失败")):
            evidence.extend(self._companion_manual_recent_test_evidence(limit=1))
        runtime_lines = [
            _single_line(line, 130)
            for line in self._companion_manual_runtime_snapshot(event).splitlines()
            if _single_line(line, 130)
        ]
        if runtime_lines and ("group" in issue_tags or recent_requested):
            evidence.append(runtime_lines[0])
        checks = [_single_line(item, 120) for item in primary.get("checks", []) if _single_line(item, 120)]
        suggestions = [_single_line(item, 120) for item in primary.get("suggestions", []) if _single_line(item, 120)]
        mentioned_keys = self._companion_manual_mentioned_config_keys(query)
        for key in self._companion_manual_config_keys_from_alias_text(query, limit=2):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        lines = [f"这次更像是“{title}”在起作用。{summary}"]
        if evidence:
            lines.append("我现在能对上的证据是：" + "；".join(evidence[:2]))
        elif recent_requested:
            lines.append("当前没有直接命中这次事件的记录，所以只能先按现象判断，别把它当成确定结论。")
        if checks:
            lines.append("先看这一处：" + checks[0])
        elif suggestions:
            lines.append("下一步先试：" + suggestions[0])
        if mentioned_keys:
            key = mentioned_keys[0]
            current = self._companion_manual_current_config_value(key)
            lines.append(f"你点名的 {self._companion_manual_config_ref(key, include_location=False)}现在是 {self._companion_manual_format_config_item_value(key, current)}。")
        elif len(selected) > 1:
            secondary = _single_line(selected[1].get("title"), 50) if isinstance(selected[1], dict) else ""
            if secondary:
                lines.append(f"如果上面不符合，再查“{secondary}”，不用一次把所有开关都翻出来。")
        return "\n".join(line for line in lines if line), selected
    def _companion_manual_local_hint_text(self, event: AstrMessageEvent, selected: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        group_note = self._companion_manual_current_group_note(event)
        if group_note:
            lines.append(group_note)
        if selected:
            primary = selected[0] if isinstance(selected[0], dict) else {}
            title = _single_line(primary.get("title"), 60)
            if title:
                lines.append("优先链路：" + title)
            checks = [
                _single_line(item, 120)
                for item in (primary.get("checks") if isinstance(primary.get("checks"), list) else [])[:2]
                if _single_line(item, 120)
            ]
            if checks:
                lines.append("可验证点：" + "；".join(checks))
        runtime = [_single_line(item, 140) for item in self._companion_manual_runtime_snapshot(event).splitlines() if _single_line(item, 140)]
        if runtime:
            lines.append("运行态：" + "；".join(runtime[:2]))
        return "\n".join(line for line in lines if line)
    async def _companion_manual_model_answer(
        self,
        event: AstrMessageEvent,
        question: str,
        local_answer: str,
        selected: list[dict[str, Any]],
        media_context: str = "",
    ) -> str:
        caller = getattr(self, "_llm_call", None)
        if not callable(caller):
            return ""
        provider_selector = getattr(self, "_task_provider", None)
        if callable(provider_selector):
            provider_id = provider_selector(
                runtime_persona_setting(self, "troubleshooting_provider_id", ""),
                runtime_persona_setting(self, "complex_reasoning_provider_id", ""),
                runtime_persona_setting(self, "llm_provider_id", ""),
                runtime_persona_setting(self, "response_review_provider_id", ""),
                runtime_persona_setting(self, "mai_style_provider_id", ""),
            )
        else:
            provider_id = str(
                runtime_persona_setting(self, "troubleshooting_provider_id", "")
                or runtime_persona_setting(self, "complex_reasoning_provider_id", "")
                or runtime_persona_setting(self, "llm_provider_id", "")
                or runtime_persona_setting(self, "response_review_provider_id", "")
                or runtime_persona_setting(self, "mai_style_provider_id", "")
                or ""
            )
        if not provider_id:
            return ""
        manual_context = self._companion_manual_context_text(selected)
        local_hint = self._companion_manual_local_hint_text(event, selected) or self._companion_manual_clean_multiline(local_answer, limit=900)
        selected_hint = (
            "关键词初筛命中：" + " / ".join(_single_line(item.get("title"), 60) for item in selected if isinstance(item, dict))
            if selected
            else "关键词初筛未命中；请直接阅读完整说明书判断，不要把“未命中”当成答案。"
        )
        mentioned_keys = self._companion_manual_mentioned_config_keys(question)
        for key in self._companion_manual_config_keys_from_alias_text(question, limit=6):
            if key not in mentioned_keys:
                mentioned_keys.append(key)
        mentioned_config_text = (
            "\n".join(f"- {self._companion_manual_config_ref(key)}" for key in mentioned_keys)
            if mentioned_keys
            else "无"
        )
        recent_context = self._companion_manual_recent_context_text(event) or "没有同一会话内的上一轮答疑上下文。"
        persona_text = ""
        refresher = getattr(self, "_refresh_default_persona_prompt", None)
        if callable(refresher):
            try:
                await asyncio.wait_for(refresher(str(getattr(event, "unified_msg_origin", "") or "")), timeout=1.5)
            except Exception:
                pass
        getter = getattr(self, "_get_default_persona_prompt", None)
        if callable(getter):
            try:
                persona_text = _single_line(getter(), 700)
            except Exception:
                persona_text = ""
        runtime = self._companion_manual_runtime_snapshot(event)
        source_context = self._companion_manual_source_context(question, selected)
        memory_context = ""
        composer = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(composer):
            try:
                memory_context = await composer(
                    kind="companion_manual_diagnosis",
                    query=(
                        f"陪伴插件答疑排障：{question}；"
                        "最近配置变动、失败日志、主动消息、群聊回复、自然语言生图、QQ空间、用户反馈、排障上下文"
                    ),
                    event=event,
                    top_k=5,
                    max_chars=900,
                    timeout_seconds=2.0,
                )
            except Exception as exc:
                logger.debug("[PrivateCompanion] 答疑 我会牢牢记住你 上下文读取失败: %s", _single_line(exc, 120))
        prompt = f"""
你是 PrivateCompanion 的插件专家答疑助手。你理解插件的功能边界、模块协作、配置、运行状态和关键实现，目标是像熟悉整个项目的维护者一样回答，而不是把问题套进关键词规则。

回答方式：
- 先直接回答用户真正问的内容。可以解释设计目的、实际调用链、模块关系、配置影响、已知限制、故障根因或改进方案，不要强行把所有问题改写成“现象排障”。
- 以当前源码摘录、配置结构和真实运行状态为最高优先级；静态能力索引和关键词候选只帮助检索，绝不是预设结论。若候选与问题不符，必须忽略。
- 用户问“能不能、为什么、怎么实现、这一改动是否有效”时，要结合实现链路进行推理，明确区分“部分解决”“完全解决”和“没有解决”。
- 用户问最近发生的具体事件时才做现场诊断；有证据就引用关键证据，没有证据就明确不确定，不要用规则命中伪装成事实。
- 回答深度由问题决定：简单问题可以短答，架构、代码或复杂排障可以充分展开，不受 3-6 行限制。
- 不要把“完整功能说明书”“关键词初筛”“本地回退”“当前配置快照”“源码检索”等内部过程说给用户。
- 不要编造不存在的配置项、模块、日志或已经执行过的操作；配置项以配置目录和源码为准。
- 解释代码、公式、耗时或单位换算时，先逐项读取原表达式和数值单位，统一到基本单位计算，再换算展示单位并做反向换算复核；明确区分代码要求时长、实际运行耗时、日志值和界面显示值。
- 不得引入源码、日志或用户材料中没有出现的运算、常数、倍率、对数或所谓解释器规则来凑结果，尤其不能凭空加入 ln、log、指数或除法。`time.sleep(10 * 60)` 就是 600 秒，即 10 分钟；若结果不符，应说明还缺哪段真实代码或日志。
- 提到配置时必须同时写中文名和参数名,格式类似“高强度唤醒阈值（group_high_intensity_wakeup_threshold）”。
- 涉及调参时，说明作用范围和副作用；只有证据支持时才给具体数值。
- 可执行改配置由本地白名单规则另行生成；你只负责解释和建议,不要声称已经修改配置。
- 语气自然、清楚，像插件作者本人在解释和排障，不要写客服套话。
- 不要说“内置说明书没匹配到”“关键词没命中”“去扩展页排障中心”这类暴露实现的话；如果不确定,就自然说明需要更具体的现象或日志。
- 不要要求用户复制文件；用户和你在同一机器上。

【用户问题】
{_single_line(question, 260)}

【当前人格/说话风格参考】
{persona_text or '未读取到人格；保持简洁、自然、温和。'}

【同一会话上一轮答疑上下文】
{recent_context}

【本轮图片/引用图片上下文】
{media_context or '本轮没有检测到随消息携带或引用的图片。'}

【我会牢牢记住你 最近排障/配置记忆】
{memory_context or '暂无可用的近期记忆。'}
使用方式：只辅助理解这台实例最近发生过什么；本地运行状态、截图和日志证据优先。不要说“我查记忆发现”。

【关键词候选（只用于检索，可能不准确）】
{selected_hint}

【插件能力知识目录】
{manual_context}

【与本题相关的当前源码/文档摘录】
{source_context or '没有检索到直接相关的源码片段；此时只能依据能力目录、配置和运行状态回答。'}

【用户明确提到的配置项】
{mentioned_config_text}

【当前运行状态快照】
{runtime or '没有拿到当前会话专项状态,只能按配置和说明书判断。'}

【本地采集到的候选证据（可能与问题无关，不得直接当结论）】
{local_hint}

请输出：
直接回答用户的问题。按问题复杂度组织内容，必要时说明调用链、依据、边界和下一步。
""".strip()
        timeout_resolver = getattr(self, "_model_timeout_seconds_for_call", None)
        answer_timeout = None
        if callable(timeout_resolver):
            try:
                answer_timeout = timeout_resolver(
                    task="companion_manual_diagnosis",
                    provider_id=provider_id,
                    timeout_key="TROUBLESHOOTING_PROVIDER_ID",
                )
            except Exception:
                answer_timeout = None
        answer_timeout = max(15.0, min(180.0, float(answer_timeout or 45.0)))
        try:
            raw = await asyncio.wait_for(
                caller(
                    prompt,
                    max_tokens=1400,
                    provider_id=provider_id,
                    task="companion_manual_diagnosis",
                    timeout_key="TROUBLESHOOTING_PROVIDER_ID",
                    timeout_seconds=answer_timeout,
                ),
                timeout=answer_timeout + 3.0,
            )
        except asyncio.TimeoutError:
            logger.info("[PrivateCompanion] 陪伴答疑模型诊断超时,回退本地说明: question=%s", _single_line(question, 120))
            return ""
        except Exception as exc:
            logger.info("[PrivateCompanion] 陪伴答疑模型诊断失败,回退本地说明: %s", _single_line(exc, 120))
            return ""
        return self._companion_manual_clean_multiline(raw, limit=1800)

    async def _companion_manual_answer(self, event: AstrMessageEvent, question: str) -> str:
        query = self._companion_manual_clean_question_text(question, 260)
        media_context = await self._companion_manual_media_context(event, query)
        if not query and media_context:
            query = "根据本轮携带或引用的图片做插件答疑/排障"
        local_answer, selected = self._companion_manual_local_answer(event, query)
        if not query:
            self._companion_manual_store_pending_config(event, query, [])
            return local_answer
        proposals = self._companion_manual_build_config_proposals(query, selected, event)
        token = self._companion_manual_store_pending_config(event, query, proposals)
        explicit_config_question = bool(self._companion_manual_mentioned_config_keys(query)) or any(
            word in query.lower()
            for word in ("配置", "设置", "参数", "阈值", "开关", "改成", "调到", "调高", "调低", "怎么开", "怎么关")
        )
        proposal_text = (
            self._companion_manual_format_config_proposals_brief(token, proposals)
            if explicit_config_question
            else ""
        )
        model_answer = await self._companion_manual_model_answer(event, query, local_answer, selected, media_context=media_context)
        if model_answer:
            answer = model_answer
        else:
            answer = self._companion_manual_fallback_answer(event, query, selected, proposals, media_context=media_context)
        if proposal_text:
            answer = f"{answer}\n\n{proposal_text}"
        self._companion_manual_store_recent_context(event, question=query, answer=answer, proposals=proposals)
        return answer

    def _daily_outfit_command_payload(self) -> tuple[str, str]:
        data = getattr(self, "data", {}) if isinstance(getattr(self, "data", {}), dict) else {}
        item = data.get("daily_outfit_photo") if isinstance(data.get("daily_outfit_photo"), dict) else {}
        today = _today_key()
        if not item:
            if not bool(runtime_persona_setting(self, 'enable_daily_outfit_photo', False)):
                return (
                    "今天还没有每日穿搭图；每日穿搭照片当前没有开启。\n"
                    "需要的话，管理员可以在配置页开启“每日穿搭照片”，或手动用：陪伴 生成穿搭。",
                    "",
                )
            return "今天还没有生成每日穿搭图。管理员可以手动用：陪伴 生成穿搭。", ""
        date_key = _single_line(item.get("date"), 20)
        error = _single_line(item.get("error"), 180)
        note = _single_line(item.get("note"), 160)
        if date_key and date_key != today:
            suffix = f"\n上一次记录是 {date_key}。"
            if error:
                suffix += f"\n上次失败原因：{error}"
            return "今天还没有新的每日穿搭图。" + suffix + "\n管理员可以手动用：陪伴 生成穿搭。", ""
        path_text = _path_text(item.get("path"), 1000)
        if not path_text:
            reason = error or note or "没有可用图片路径"
            retry_count = int(item.get("retry_count", 0) or 0)
            retry_max = 5
            if retry_count > 0 and retry_count < retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n正在自动重试（第{retry_count}/{retry_max}次），稍后再来看看，或管理员手动用：陪伴 生成穿搭。", ""
            elif retry_count >= retry_max:
                return f"今天的每日穿搭图还没生成成功：{reason}\n已重试{retry_max}次仍未成功，管理员可以手动用：陪伴 生成穿搭。", ""
            return f"今天的每日穿搭图还没生成成功：{reason}\n管理员可以手动用：陪伴 生成穿搭。", ""
        try:
            path = Path(path_text).expanduser()
            if not path.is_absolute():
                path = Path(self.data_dir) / path
            path = path.resolve()
        except Exception:
            path = Path(path_text)
        try:
            exists = path.exists() and path.is_file()
        except (OSError, ValueError):
            exists = False
        if not exists:
            return "今天的每日穿搭图记录存在，但图片文件已经找不到了。\n管理员可以手动用：陪伴 生成穿搭。", ""
        if path.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
            return "今天的每日穿搭图记录存在，但图片格式不支持发送。管理员可以重新生成一次。", ""
        meta_parts = []
        generated_at = _safe_float(item.get("generated_at"), 0.0, 0.0)
        formatter = getattr(self, "_format_timestamp_elapsed", None)
        if generated_at > 0 and callable(formatter):
            meta_parts.append(f"生成：{formatter(generated_at)}")
        backend = _single_line(item.get("backend"), 40)
        if backend:
            meta_parts.append(f"后端：{backend}")
        caption = "今天的穿搭图在这里。"
        if meta_parts:
            caption += "\n" + "｜".join(meta_parts)
        return caption, str(path)

    def _photo_reference_image_dir(self) -> Path:
        target_dir = Path(self.data_dir) / "photo_reference_images"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    def _photo_reference_stem(self, stem: str = "reference") -> str:
        clean = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(stem or "reference")).strip("._")
        if not clean:
            clean = "reference"
        return f"{clean}_{int(_now_ts() * 1000)}_{uuid.uuid4().hex[:8]}"

    def _photo_reference_path_within_data_dir(self, path: Path) -> bool:
        """Return whether a reference path is inside this plugin's data tree."""
        try:
            root = Path(self.data_dir).resolve()
            resolved = path.resolve()
        except Exception:
            return False
        try:
            return resolved.is_relative_to(root)
        except AttributeError:  # Python < 3.9
            return str(resolved) == str(root) or str(resolved).startswith(str(root) + os.sep)

    def _photo_reference_copy_local_file(
        self,
        source_path: Path,
        *,
        stem: str = "reference",
        trusted: bool = True,
    ) -> str:
        """Copy a reference image into the plugin-owned reference directory.

        Untrusted/model-controlled sources may only read files below the plugin
        data directory. Explicit administrator configuration keeps the legacy
        trusted behavior for paths outside that directory.
        """
        try:
            resolved = source_path.resolve()
        except Exception:
            resolved = source_path
        if not trusted and not self._photo_reference_path_within_data_dir(resolved):
            logger.warning(
                "[PrivateCompanion] 参考图越权本地路径已拒绝: %s",
                _single_line(str(resolved), 200),
            )
            return ""
        if not resolved.exists() or not resolved.is_file():
            return ""
        suffix = resolved.suffix.lower()
        if suffix not in _PHOTO_REFERENCE_SUFFIXES:
            return ""
        target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
        shutil.copy2(resolved, target)
        return str(target.resolve())

    def _photo_reference_write_data_image(self, source: str, *, stem: str = "reference") -> str:
        text = str(source or "").strip()
        try:
            if text.startswith("base64://"):
                raw = base64.b64decode(text[len("base64://"):], validate=False)
                suffix = ".jpg"
            elif text.startswith("data:") and "," in text:
                meta, payload = text.split(",", 1)
                if ";base64" not in meta.lower():
                    return ""
                raw = base64.b64decode(payload, validate=False)
                lowered = meta.lower()
                suffix = ".png" if "png" in lowered else ".webp" if "webp" in lowered else ".jpg"
            else:
                return ""
            if not raw:
                return ""
            target = self._photo_reference_image_dir() / f"{self._photo_reference_stem(stem)}{suffix}"
            target.write_bytes(raw)
            return str(target.resolve())
        except Exception:
            return ""

    async def _photo_reference_source_to_stable_path(
        self,
        source: str,
        *,
        stem: str = "reference",
        event: AstrMessageEvent | None = None,
        trusted: bool = True,
    ) -> str:
        """Normalize a reference source into a stable plugin-local path.

        Model-controlled sources are restricted to plugin data paths and public
        remote hosts; administrator-configured sources retain the legacy trust
        boundary.
        """
        text = str(source or "").strip()
        if not text:
            return ""
        data_path = self._photo_reference_write_data_image(text, stem=stem)
        if data_path:
            return data_path
        if re.match(r"^https?://", text, flags=re.I):
            downloader = getattr(self, "_persist_private_remote_image_source", None)
            if callable(downloader):
                try:
                    downloaded = await downloader(
                        text,
                        self._photo_reference_image_dir(),
                        self._photo_reference_stem(f"{stem}_remote"),
                        public_hosts_only=not trusted,
                    )
                except TypeError:
                    # Older mixins may not accept the security keyword; reject
                    # untrusted input instead of silently downgrading it.
                    if not trusted:
                        return ""
                    try:
                        downloaded = await downloader(
                            text,
                            self._photo_reference_image_dir(),
                            self._photo_reference_stem(f"{stem}_remote"),
                        )
                    except Exception:
                        downloaded = ""
                except Exception:
                    downloaded = ""
                if downloaded:
                    copied = self._photo_reference_copy_local_file(
                        Path(downloaded),
                        stem=stem,
                        trusted=trusted,
                    )
                    if copied:
                        return copied
                    if trusted:
                        return str(downloaded)
            return ""
        local_text = text[len("file://"):] if text.startswith("file://") else text
        try:
            copied = self._photo_reference_copy_local_file(
                Path(local_text),
                stem=stem,
                trusted=trusted,
            )
            if copied:
                return copied
        except (OSError, ValueError):
            pass
        resolver = getattr(self, "_qzone_resolve_onebot_image_source", None)
        if callable(resolver) and event is not None:
            try:
                resolved = await resolver(event, text)
            except Exception:
                resolved = ""
            if resolved and resolved != text:
                return await self._photo_reference_source_to_stable_path(
                    resolved,
                    stem=stem,
                    event=event,
                    trusted=trusted,
                )
        return ""

    async def _photo_reference_sources_from_current_event(self, event: AstrMessageEvent, user_id: str) -> list[str]:
        sources: list[str] = []

        # Keep persisted message images in the same platform/account scope as
        # the active user profile.  Callers from older command paths may still
        # pass the raw sender ID, so resolve from the event here as the single
        # boundary rather than relying on every caller to do it correctly.
        resolver = getattr(self, "_private_user_id_for_event", None)
        if callable(resolver):
            try:
                raw_sender = event.get_sender_id()
            except Exception:
                raw_sender = ""
            if raw_sender:
                try:
                    scoped = _single_line(resolver(event, raw_sender), 160)
                except Exception:
                    scoped = ""
                if scoped:
                    user_id = scoped

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        persister = getattr(self, "_persist_private_inbound_images", None)
        if callable(persister):
            try:
                for source in await persister(event, user_id):
                    add(source)
            except Exception:
                pass
        raw_extractor = getattr(self, "_raw_private_image_sources", None)
        if callable(raw_extractor):
            try:
                for source in raw_extractor(event):
                    add(source)
            except Exception:
                pass
        return sources

    def _photo_reference_sources_from_reply_cache(self, event: AstrMessageEvent) -> list[str]:
        sources: list[str] = []

        def add(value: Any) -> None:
            text = str(value or "").strip()
            if text and text not in sources:
                sources.append(text)

        cleanup = getattr(self, "_cleanup_recall_message_cache", None)
        if callable(cleanup):
            try:
                cleanup()
            except Exception:
                pass
        cache = getattr(self, "_recall_message_cache", None)
        if not isinstance(cache, dict):
            return sources
        id_getter = getattr(self, "_event_reply_message_ids", None)
        message_ids = id_getter(event) if callable(id_getter) else []
        scope_getter = getattr(self, "_event_scope_key", None)
        current_scope = _single_line(scope_getter(event), 160) if callable(scope_getter) else ""
        item_getter = getattr(self, "_recall_image_items_from_snapshot", None)
        for message_id in message_ids:
            snapshot = cache.get(message_id)
            if not isinstance(snapshot, dict):
                continue
            snapshot_scope = _single_line(snapshot.get("scope"), 160)
            if current_scope and snapshot_scope and snapshot_scope != current_scope:
                continue
            if callable(item_getter):
                try:
                    items = item_getter(snapshot)
                except Exception:
                    items = []
            else:
                raw_items = snapshot.get("image_items") if isinstance(snapshot.get("image_items"), list) else []
                items = [item for item in raw_items if isinstance(item, dict)]
            for item in items:
                if not isinstance(item, dict):
                    continue
                tier = _single_line(item.get("tier"), 40)
                source = str(item.get("source") or "").strip()
                if not source or tier in {"placeholder", "platform_file"}:
                    continue
                add(source)
            for source in snapshot.get("images") if isinstance(snapshot.get("images"), list) else []:
                add(source)
        return sources

    async def _photo_reference_sources_from_reply_event(self, event: AstrMessageEvent) -> list[str]:
        cached = getattr(event, "_private_companion_photo_reply_sources", None)
        if isinstance(cached, list):
            return [str(item).strip() for item in cached if str(item or "").strip()]
        sources: list[str] = []
        finder = getattr(self, "_find_reply_image_sources_for_event", None)
        if callable(finder):
            try:
                for source in await finder(event):
                    text = str(source or "").strip()
                    if text and text not in sources:
                        sources.append(text)
            except Exception:
                sources = []
        try:
            setattr(event, "_private_companion_photo_reply_sources", list(sources))
        except Exception:
            pass
        return sources

    async def _photo_reference_event_bound_stable_path(
        self,
        event: AstrMessageEvent,
        user_id: str,
        source: str,
        *,
        stem: str = "event_reference",
    ) -> str:
        """Persist a model-supplied source only when the active event owns it."""
        requested = str(source or "").strip()
        if not requested:
            return ""

        cache_name = "_private_companion_event_bound_reference_sources"
        cached = getattr(event, cache_name, None)
        if isinstance(cached, tuple):
            candidates = list(cached)
        else:
            candidates: list[str] = []

            def add(values: Any) -> None:
                for value in values if isinstance(values, (list, tuple, set)) else ():
                    text = str(value or "").strip()
                    if text and text not in candidates:
                        candidates.append(text)

            add(await self._photo_reference_sources_from_current_event(event, user_id))
            add(self._photo_reference_sources_from_reply_cache(event))
            add(await self._photo_reference_sources_from_reply_event(event))
            try:
                setattr(event, cache_name, tuple(candidates))
            except Exception:
                pass

        def local_identity(value: str) -> str:
            text = str(value or "").strip()
            if not text or re.match(r"^https?://", text, flags=re.I):
                return ""
            if text.startswith("file://"):
                text = text[len("file://"):]
            try:
                return os.path.normcase(str(Path(text).expanduser().resolve()))
            except (OSError, ValueError):
                return ""

        requested_local = local_identity(requested)
        matched = next(
            (
                candidate
                for candidate in candidates
                if candidate == requested
                or (
                    requested_local
                    and local_identity(candidate) == requested_local
                )
            ),
            "",
        )
        if not matched:
            return ""
        return await self._photo_reference_source_to_stable_path(
            matched,
            stem=stem,
            event=event,
            trusted=True,
        )

    async def _photo_reference_image_from_command_context(
        self,
        event: AstrMessageEvent,
        user_id: str,
    ) -> tuple[str, str, bool]:
        images, saw_image = await self._photo_reference_images_from_command_context(event, user_id, limit=1)
        if images:
            return images[0][0], images[0][1], True
        return "", "", saw_image

    async def _photo_reference_images_from_command_context(
        self,
        event: AstrMessageEvent,
        user_id: str,
        *,
        limit: int = 12,
    ) -> tuple[list[tuple[str, str]], bool]:
        images: list[tuple[str, str]] = []
        saw_image = False
        seen_sources: set[str] = set()
        seen_fingerprints: set[str] = set()

        def file_fingerprint(path_text: str) -> str:
            try:
                path = Path(path_text).resolve()
                if not path.is_file():
                    return ""
                digest = hashlib.sha256()
                with path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        digest.update(chunk)
                return f"{path.stat().st_size}:{digest.hexdigest()}"
            except (OSError, ValueError):
                return ""

        def remove_duplicate_copy(path_text: str) -> None:
            try:
                path = Path(path_text).resolve()
                base = self._photo_reference_image_dir().resolve()
                if path.is_file() and path.is_relative_to(base):
                    path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass

        async def collect(sources: list[str], label: str, stem: str) -> None:
            nonlocal saw_image
            for source in sources:
                saw_image = True
                source_key = str(source or "").strip()
                if not source_key or source_key in seen_sources:
                    continue
                seen_sources.add(source_key)
                if len(images) >= max(1, limit):
                    return
                path = await self._photo_reference_source_to_stable_path(source, stem=stem, event=event)
                if not path or any(existing_path == path for existing_path, _ in images):
                    continue
                fingerprint = file_fingerprint(path)
                if fingerprint and fingerprint in seen_fingerprints:
                    remove_duplicate_copy(path)
                    continue
                if fingerprint:
                    seen_fingerprints.add(fingerprint)
                images.append((path, label))

        await collect(await self._photo_reference_sources_from_current_event(event, user_id), "随消息发送的图片", "message_library")
        if len(images) < max(1, limit):
            await collect(self._photo_reference_sources_from_reply_cache(event), "引用消息里的图片", "reply_library")
        if len(images) < max(1, limit):
            await collect(await self._photo_reference_sources_from_reply_event(event), "引用消息里的图片", "reply_library")
        return images, saw_image

    def _resolve_photo_reference_command_path(self, value: str) -> tuple[str, str]:
        raw = _path_text(value, 1000)
        if not raw:
            return "", "请这样设置：陪伴 参考图 <本地图片路径或图片URL>"
        if re.match(r"^https?://", raw, flags=re.I):
            return raw, ""
        expanded = os.path.expandvars(os.path.expanduser(raw))
        candidates = [Path(expanded)]
        if not candidates[0].is_absolute():
            candidates.append(Path(self.data_dir) / expanded)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if not resolved.exists() or not resolved.is_file():
                continue
            if resolved.suffix.lower() not in _PHOTO_REFERENCE_SUFFIXES:
                return "", "参考图只支持 png、jpg、jpeg、webp。"
            return str(resolved), ""
        return "", "没有找到这张本地图片。请确认路径存在，并且 Bot 所在机器能访问；也可以直接填写 http(s) 图片 URL。"

    async def _set_photo_reference_config_path(self, path: str) -> bool:
        clean = _path_text(path, 1000)
        if runtime_persona_setting(self, 'photo_reference_catalog', None) is None:
            previous = _path_text(runtime_persona_setting(self, 'photo_persona_reference_image_path', ""), 1000)
            self.photo_persona_reference_image_path = clean
            try:
                saved = _set_into_config(self.config, "photo_persona_reference_image_path", clean)
                if saved and not await self._save_config_if_possible():
                    self.photo_persona_reference_image_path = previous
                    _set_into_config(self.config, "photo_persona_reference_image_path", previous)
                    return False
                return bool(saved)
            except Exception:
                self.photo_persona_reference_image_path = previous
                try:
                    _set_into_config(self.config, "photo_persona_reference_image_path", previous)
                except Exception:
                    pass
                return False
        try:
            catalog = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            persona = next(
                (item for item in catalog if isinstance(item, PhotoReference) and item.kind == "persona"),
                None,
            )
            if clean and persona is not None:
                updated = tuple(replace(item, source=clean) if item.id == persona.id else item for item in catalog)
            elif clean:
                updated = add_reference(
                    catalog,
                    kind="persona",
                    source=clean,
                    note="基础人物身份和外貌参考；没有更匹配的服装场景参考图时使用",
                    preset_names=self._photo_generation_scene_presets().keys(),
                )
            elif persona is not None:
                updated = delete_reference(catalog, persona.id)
            else:
                updated = catalog
            return await self._set_photo_reference_catalog_config(updated)
        except (CatalogValidationError, KeyError, TypeError, ValueError) as exc:
            logger.warning("[PrivateCompanion] 保存 persona 参考图失败: %s", _single_line(exc, 160))
            return False

    async def _set_photo_reference_catalog_config(self, items: Any) -> bool:
        if bool(getattr(self, "photo_reference_catalog_read_only", False)):
            logger.warning("[PrivateCompanion] 参考图目录当前为只读状态，拒绝覆盖原配置；请在管理页校验并保存目录")
            return False
        previous = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
        previous_version = _safe_int(runtime_persona_setting(self, 'photo_reference_catalog_version', 0), 0, 0)
        previous_user_cleared = bool(runtime_persona_setting(self, 'photo_reference_catalog_user_cleared', False))
        preset_names = self._photo_generation_scene_presets().keys()
        try:
            serialized = validate_and_serialize(items, preset_names=preset_names)
            loaded = load_catalog(serialized, catalog_version=CATALOG_VERSION, preset_names=preset_names)
            previous_serialized = validate_and_serialize(previous, preset_names=preset_names)
            self.photo_reference_catalog = loaded.references
            self.photo_reference_catalog_version = CATALOG_VERSION
            self.photo_reference_catalog_user_cleared = not bool(loaded.references)
            catalog_set = _set_into_config(self.config, "photo_reference_catalog", serialized)
            version_set = _set_into_config(self.config, "photo_reference_catalog_version", CATALOG_VERSION)
            cleared_set = _set_into_config(
                self.config,
                "photo_reference_catalog_user_cleared",
                runtime_persona_setting(self, 'photo_reference_catalog_user_cleared', False),
            )
            if not catalog_set or not version_set or not cleared_set or not await self._save_config_if_possible():
                self.photo_reference_catalog = previous
                self.photo_reference_catalog_version = previous_version
                self.photo_reference_catalog_user_cleared = previous_user_cleared
                _set_into_config(self.config, "photo_reference_catalog", previous_serialized)
                _set_into_config(self.config, "photo_reference_catalog_version", previous_version)
                _set_into_config(self.config, "photo_reference_catalog_user_cleared", previous_user_cleared)
                return False
            return True
        except Exception as exc:
            self.photo_reference_catalog = previous
            self.photo_reference_catalog_version = previous_version
            self.photo_reference_catalog_user_cleared = previous_user_cleared
            try:
                _set_into_config(
                    self.config,
                    "photo_reference_catalog",
                    validate_and_serialize(previous, preset_names=preset_names),
                )
                _set_into_config(self.config, "photo_reference_catalog_version", previous_version)
                _set_into_config(self.config, "photo_reference_catalog_user_cleared", previous_user_cleared)
            except Exception:
                pass
            logger.warning("[PrivateCompanion] 保存规范参考图目录失败: %s", _single_line(exc, 180))
            return False

    async def _set_photo_reference_library_config(self, items: list[Any]) -> bool:
        if runtime_persona_setting(self, 'photo_reference_catalog', None) is None:
            normalized: list[Any] = []
            seen_sources: set[str] = set()
            for raw_item in items[:24]:
                if isinstance(raw_item, dict):
                    source = _path_text(raw_item.get("source") or raw_item.get("path") or raw_item.get("url"), 1000)
                    if not source or source in seen_sources:
                        continue
                    seen_sources.add(source)
                    normalized.append(dict(raw_item))
                    continue
                text = str(raw_item or "").strip()
                source = re.split(r"\s*(?:\|\||｜｜)\s*", text, maxsplit=1)[0].strip() if text else ""
                if text and source not in seen_sources:
                    seen_sources.add(source)
                    normalized.append(text[:3000])
            previous = list(runtime_persona_setting(self, 'photo_reference_library', []) or [])
            self.photo_reference_library = normalized
            try:
                saved = _set_into_config(self.config, "photo_reference_library", normalized)
                if saved and not await self._save_config_if_possible():
                    self.photo_reference_library = previous
                    _set_into_config(self.config, "photo_reference_library", previous)
                    return False
                return bool(saved)
            except Exception:
                self.photo_reference_library = previous
                try:
                    _set_into_config(self.config, "photo_reference_library", previous)
                except Exception:
                    pass
                return False
        try:
            preset_names = self._photo_generation_scene_presets().keys()
            loaded = load_catalog(
                [],
                catalog_version=0,
                legacy_library=items,
                preset_names=preset_names,
            )
            catalog = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            kept = tuple(
                item
                for item in catalog
                if isinstance(item, PhotoReference) and item.kind != "library"
            )
            return await self._set_photo_reference_catalog_config((*kept, *loaded.references))
        except (CatalogValidationError, TypeError, ValueError) as exc:
            logger.warning("[PrivateCompanion] 保存兼容参考图库失败: %s", _single_line(exc, 160))
            return False

    async def _photo_reference_library_command_payload(
        self,
        event: AstrMessageEvent,
        user_id: str,
        value: str = "",
    ) -> tuple[str, str]:
        action = str(value or "").strip()
        entries_getter = getattr(self, "_photo_reference_library_entries", None)
        entries = entries_getter() if callable(entries_getter) else []
        if action in {"", "列表", "查看", "状态", "list", "show"}:
            if not entries:
                return (
                    "参考图库目前为空。\n"
                    "发送一张或多张图片并附上：陪伴 参考图库 添加 居家服，在家、卧室、睡前使用\n"
                    "也可以在陪伴面板按“路径或 URL || 用途注释”一行一张填写。"
                ), ""
            lines = [f"参考图库：{len(entries)}/24 张"]
            for index, item in enumerate(entries, start=1):
                summary = [
                    f"职责={','.join(item.get('reference_roles') or []) or '-'}",
                    f"服装={_single_line(item.get('outfit_category'), 50) or '-'}",
                    f"锁定={'是' if item.get('outfit_lock_default') else '否'}",
                    f"场景={','.join(item.get('scene_categories') or []) or '-'}",
                    f"时间={','.join(item.get('time_categories') or []) or '-'}",
                    f"预设={_single_line(item.get('preferred_preset'), 60) or '-'}",
                ]
                lines.append(
                    f"{index}. {_single_line(item.get('note'), 160)}\n"
                    f"   ID={_single_line(item.get('id'), 80)}｜{'｜'.join(summary)}\n"
                    f"   {_single_line(item.get('source'), 260)}"
                )
            lines.append("预览：陪伴 参考图库 预览 编号；删除：陪伴 参考图库 删除 编号或ID")
            return "\n".join(lines), ""
        preview_match = re.match(r"^(?:预览|查看|preview|show)\s*(\d{1,2})$", action, flags=re.I)
        if preview_match:
            index = int(preview_match.group(1)) - 1
            if not 0 <= index < len(entries):
                return "没有这个编号的参考图。", ""
            item = entries[index]
            path = self._photo_reference_local_path(item.get("source", "")) if callable(getattr(self, "_photo_reference_local_path", None)) else ""
            if not path and re.match(r"^https?://", item.get("source", ""), flags=re.I):
                path = await self._photo_reference_source_to_stable_path(item["source"], stem=f"library_preview_{index + 1}")
            if not path:
                return f"第 {index + 1} 张参考图当前不可用，请检查路径或 URL。", ""
            summary = [
                f"职责={','.join(item.get('reference_roles') or []) or '-'}",
                f"服装={_single_line(item.get('outfit_category'), 50) or '-'}",
                f"锁定={'是' if item.get('outfit_lock_default') else '否'}",
                f"场景={','.join(item.get('scene_categories') or []) or '-'}",
                f"时间={','.join(item.get('time_categories') or []) or '-'}",
                f"预设={_single_line(item.get('preferred_preset'), 60) or '-'}",
            ]
            return (
                f"参考图 {index + 1}：{_single_line(item.get('note'), 260)}\n"
                f"ID={_single_line(item.get('id'), 80)}｜{'｜'.join(summary)}",
                path,
            )
        role_match = re.match(
            r"^(?:设置|设为|职责)\s+([0-9A-Za-z_-]{1,80})\s+(仅身份|仅服装|仅姿势|仅场景|仅画风|身份|服装|姿势|场景|画风)$",
            action,
            flags=re.I,
        )
        if role_match:
            identifier, shortcut = role_match.groups()
            if identifier.isdigit():
                index = int(identifier) - 1
                if not 0 <= index < len(entries):
                    return "没有这个编号的参考图。", ""
                selected = entries[index]
            else:
                selected = next((item for item in entries if item.get("id") == identifier), None)
                if selected is None:
                    return "没有这个 ID 的参考图。", ""
                index = entries.index(selected)
            role = {
                "仅身份": "identity",
                "身份": "identity",
                "仅服装": "outfit",
                "服装": "outfit",
                "仅姿势": "pose",
                "姿势": "pose",
                "仅场景": "scene",
                "场景": "scene",
                "仅画风": "style",
                "画风": "style",
            }[shortcut]
            updated: list[PhotoReference] = []
            for reference in tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()):
                if not isinstance(reference, PhotoReference):
                    continue
                if reference.id == selected.get("id"):
                    reference = replace(
                        reference,
                        reference_roles=(role,),
                        outfit_lock_default=role == "outfit",
                        metadata_source="configured",
                    )
                updated.append(reference)
            saved = await self._set_photo_reference_catalog_config(tuple(updated))
            return (
                f"已将参考图 {index + 1} 设置为仅承担 {role} 职责。"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), ""
        delete_match = re.match(r"^(?:删除|移除|delete|remove)\s+([0-9A-Za-z_-]{1,80})$", action, flags=re.I)
        if delete_match:
            identifier = delete_match.group(1)
            if identifier.isdigit():
                index = int(identifier) - 1
                if not 0 <= index < len(entries):
                    return "没有这个编号的参考图。", ""
                removed = entries[index]
            else:
                removed = next((item for item in entries if item.get("id") == identifier), None)
                if removed is None:
                    return "没有这个 ID 的参考图。", ""
                index = entries.index(removed)
            try:
                kept = delete_reference(
                    tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ()),
                    removed["id"],
                )
            except KeyError:
                return "这张参考图已经不存在。", ""
            saved = await self._set_photo_reference_catalog_config(kept)
            return (
                f"已从参考图库删除第 {index + 1} 张：{_single_line(removed.get('note'), 160)}"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), ""
        if action in {"清空", "全部清空", "clear", "clear all"}:
            kept = tuple(
                item
                for item in (runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
                if isinstance(item, PhotoReference) and item.kind != "library"
            )
            saved = await self._set_photo_reference_catalog_config(kept)
            return "已清空参考图库。" + ("" if saved else "\n但配置保存可能失败，请到面板确认。"), ""

        add_match = re.match(r"^(?:添加|上传|新增|add|upload)(?:\s+([\s\S]*))?$", action, flags=re.I)
        if add_match:
            note = _single_line(add_match.group(1), 500) or "通用人物参考图；没有更具体的服装或场景匹配时使用"
            images, saw_image = await self._photo_reference_images_from_command_context(event, user_id, limit=12)
            if not images:
                if saw_image:
                    return "找到了图片，但没能保存为参考图；请确认是 png、jpg、jpeg 或 webp。", ""
                return "请把一张或多张图片与命令一起发送，或回复图片后发送“陪伴 参考图库 添加 用途注释”。", ""
            current = tuple(runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
            available = max(0, 24 - len(entries))
            added = images[:available]
            if not added:
                return "参考图库已达到 24 张上限，请先删除不用的图片。", ""
            try:
                updated = current
                for path, _label in added:
                    updated = add_reference(
                        updated,
                        kind="library",
                        source=path,
                        note=note,
                        preset_names=self._photo_generation_scene_presets().keys(),
                    )
            except CatalogValidationError as exc:
                return f"参考图元数据校验失败：{_single_line(exc, 300)}", ""
            saved = await self._set_photo_reference_catalog_config(updated)
            return (
                f"已向参考图库添加 {len(added)} 张图片。\n用途注释：{note}\n"
                "生成时会结合地点、服装和画面要求自动选择其中一张；今日穿搭图不再无条件优先。"
                + ("" if saved else "\n但配置保存可能失败，请到面板确认。")
            ), added[0][0]
        return (
            "可用命令：\n"
            "陪伴 参考图库 添加 <用途注释>（可同时携带多张图）\n"
            "陪伴 参考图库 列表\n"
            "陪伴 参考图库 预览 <编号>\n"
            "陪伴 参考图库 设置 <编号> <仅身份|仅服装|仅姿势|仅场景|仅画风>\n"
            "陪伴 参考图库 删除 <编号>\n"
            "陪伴 参考图库 清空"
        ), ""

    async def _photo_reference_command_text(self, event: AstrMessageEvent, user_id: str, value: str = "") -> str:
        text, _ = await self._photo_reference_command_payload(event, user_id, value)
        return text

    async def _photo_reference_command_payload(self, event: AstrMessageEvent, user_id: str, value: str = "") -> tuple[str, str]:
        action = _single_line(value, 1000)
        if action in {"清空", "删除", "移除", "clear", "none", "空"}:
            saved = await self._set_photo_reference_config_path("")
            return "已清空主动自拍人设参考图。" + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。"), ""
        force_image = action in {"图片", "这张", "这张图", "引用", "引用图", "引用图片", "设置", "更换", "更新", "添加", "上传", "用这张", "使用这张"}
        preview_actions = {"查看", "状态", "当前", "预览", "检查", "发出来", "发图", "看看", "current", "show", "preview"}
        if action in preview_actions:
            force_image = False
        if not action or force_image:
            image_path, image_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            if image_path:
                saved = await self._set_photo_reference_config_path(image_path)
                enabled_note = (
                    "参考图一致性已开启，会在 selfie/人像/头像/角色表情包自动生图里使用。"
                    if runtime_persona_setting(self, 'enable_photo_reference_image', False)
                    else "参考图路径已保存，但“参考图一致性”当前关闭；需要自动用于自拍/头像/角色表情包时，请在生图/拍照能力详情里开启。"
                )
                return (
                    f"已把{image_label}设为主动自拍人设参考图：\n"
                    f"{image_path}\n"
                    f"{enabled_note}\n"
                    "ComfyUI 需要支持 images=1 的自拍工作流。"
                    + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
                ), image_path
            if force_image:
                if saw_image:
                    return "找到了图片，但没能保存成参考图。参考图只支持 png、jpg、jpeg、webp；也可能是平台只给了图片 file id，拿不到原图。", ""
                return "没有在这条消息或引用消息里找到图片。可以发送图片并附上“陪伴 参考图”，或回复一条近期图片消息发送“陪伴 参考图”。", ""
        if not action or action in preview_actions:
            persona = next(
                (
                    item
                    for item in (runtime_persona_setting(self, 'photo_reference_catalog', ()) or ())
                    if isinstance(item, PhotoReference) and item.kind == "persona"
                ),
                None,
            )
            configured = _path_text(persona.source if persona is not None else "", 1000)
            resolved = self._photo_persona_reference_image_path() if callable(getattr(self, "_photo_persona_reference_image_path", None)) else ""
            enabled = bool(runtime_persona_setting(self, 'enable_photo_reference_image', False))
            if not configured:
                return (
                    f"参考图一致性：{'开启' if enabled else '关闭'}\n"
                    "当前没有设置主动自拍人设参考图。\n"
                    "设置方式：陪伴 参考图 <本地图片路径或图片URL>；也可以发送图片并附上“陪伴 参考图”。"
                ), ""
            if not resolved and re.match(r"^https?://", configured.strip(), flags=re.I):
                async_resolver = getattr(self, "_photo_persona_reference_image_path_async", None)
                if enabled and callable(async_resolver):
                    try:
                        resolved = _path_text(await async_resolver(), 1000)
                    except Exception as exc:
                        logger.info("[PrivateCompanion] 参考图查看时 URL 下载失败: %s", _single_line(exc, 120))
                        resolved = ""
            status = "可用" if resolved else "URL 待首次使用时下载" if re.match(r"^https?://", configured.strip(), flags=re.I) else "路径不可用或格式不支持"
            return (
                f"参考图一致性：{'开启' if enabled else '关闭'}\n"
                "当前主动自拍人设参考图：\n"
                f"{configured}\n"
                f"状态：{status}"
                + ("" if enabled else "\n提示：开关关闭时不会自动用于自拍/头像/角色表情包。")
                + (f"\n实际使用文件：{resolved}" if resolved and resolved != configured else "")
            ), resolved
        path, error = self._resolve_photo_reference_command_path(action)
        if error:
            return error, ""
        stable_path = await self._photo_reference_source_to_stable_path(path, stem="manual") or path
        saved = await self._set_photo_reference_config_path(stable_path)
        enabled_note = (
            "参考图一致性已开启，会在 selfie/人像/头像/角色表情包自动生图里使用。"
            if runtime_persona_setting(self, 'enable_photo_reference_image', False)
            else "参考图路径已保存，但“参考图一致性”当前关闭；需要自动用于自拍/头像/角色表情包时，请在生图/拍照能力详情里开启。"
        )
        return (
            "已设置主动自拍人设参考图：\n"
            f"{stable_path}\n"
            f"{enabled_note}\n"
            "ComfyUI 需要支持 images=1 的自拍工作流。"
            + ("" if saved else "\n但配置保存可能失败，请稍后在配置页确认。")
        ), stable_path

    def _natural_language_photo_explicit_plugin_request(self, text: str) -> bool:
        compact = re.sub(r"\s+", "", str(text or ""))
        if not compact:
            return False
        plugin_hit = any(
            token in compact
            for token in (
                "插件能力",
                "插件生图",
                "插件画图",
                "用插件",
                "走插件",
                "陪伴能力",
                "陪伴插件",
                "本插件",
            )
        )
        if not plugin_hit:
            return False
        return any(
            token in compact
            for token in (
                "画",
                "绘图",
                "生图",
                "出图",
                "生成图",
                "生成图片",
                "图片",
                "照片",
                "改图",
                "修图",
                "重绘",
            )
        )

    def _natural_language_photo_disabled_text(self, reason: str = "natural_off") -> str:
        if reason == "photo_off":
            return (
                "插件的主动拍照/生图总开关现在没开，所以不能走插件生图链路。\n"
                "位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图。"
            )
        return (
            "插件的规则快判生图/改图入口现在没开，所以不会在主链前直接接管这句。\n"
            "如果想让普通聊天触发生图，建议使用 tool_first 模式，由主链调用 pc_generate_photo；位置：拓展页 -> 功能开关 -> 长线主动 -> 主动拍照/生图详情 -> 非指令生图/改图。"
        )

    def _natural_language_photo_intent(
        self,
        text: str,
        *,
        has_reference: bool = False,
        directed: bool = False,
    ) -> dict[str, Any]:
        raw = re.sub(r"\[CQ:image,[^\]]+\]", "", str(text or ""))
        raw = re.sub(r"\[CQ:at,[^\]]+\]", "", raw)
        raw = re.sub(r"\[(?:At|@):[^\]]+\]", "", raw, flags=re.I)
        raw = re.sub(r"\[(?:引用消息|回复消息|reply)\]", "", raw, flags=re.I)
        raw = _single_line(raw, 800)
        if not raw:
            return {}
        compact = re.sub(r"\s+", "", raw)
        selfie_markers = (
            "自拍",
            "拍照",
            "拍张照",
            "拍一张照",
            "拍一张照片",
            "拍张照片",
            "来张自拍",
            "发张自拍",
            "发一张自拍",
            "腿照",
            "脚照",
            "手照",
            "全身照",
            "半身照",
            "近照",
            "生活照",
            "穿搭照",
        )
        character_photo_matcher = getattr(self, "_character_photo_request_matches", None)
        selfie_hit = any(marker in compact for marker in selfie_markers) or bool(
            callable(character_photo_matcher) and character_photo_matcher(raw)
        )
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(raw)
        draw_visual_targets = ("图片", "照片", "插画", "头像", "壁纸", "表情包", "自拍", "拍照", "画卷", "图")
        edit_visual_targets = draw_visual_targets + ("这张", "这个图", "引用图")
        edit_strong_markers = ("改图", "修图", "重绘", "p图", "P图", "p一下", "P一下")
        edit_operation_markers = ("改成", "改为", "改一下", "p成", "P成", "换成", "变成", "加上", "加个", "去掉", "去除")
        draw_patterns = (
            r"(?:帮我|给我|替我|请你|麻烦你)?(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?.{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画一张|画个|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:帮我|给我|替我|请你|麻烦你)(?:画|生成|做|出).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成|生成一张|生一张|做一张|做个|出一张)(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:画一张|画个|生成一张|生一张|做一张|做个|出一张).{0,80}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
            r"(?:来|整)(?:一张|张|个).{0,40}?(?:图片|照片|插画|头像|壁纸|表情包|图)",
        )
        draw_hit = any(re.search(pattern, raw, flags=re.I) for pattern in draw_patterns)
        if draw_hit and not any(token in compact for token in draw_visual_targets):
            draw_hit = False
        if selfie_hit and directed:
            draw_hit = True
        if not draw_hit and explicit_plugin_request:
            draw_hit = True
        if not draw_hit and directed:
            bare_draw_patterns = (
                r"^(?:帮我|给我|替我|请你|请|麻烦你)?(?:画一张|画个|画一下|画一个|画|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个)\S{1,120}",
                r"^(?:帮我|给我|替我|请你|请|麻烦你)(?:画|生成|做|出|整)\S{1,120}",
            )
            draw_hit = any(re.search(pattern, compact, flags=re.I) for pattern in bare_draw_patterns)
            if draw_hit and re.search(r"(?:画个饼|画饼|规划|画重点|画大饼|画风|图个|图啥|图什么)", compact, flags=re.I):
                draw_hit = False
        edit_hit = False
        if has_reference:
            explicit_visual_target = any(token in compact for token in edit_visual_targets)
            strong_edit = any(marker in compact for marker in edit_strong_markers)
            operation_edit = any(marker in compact for marker in edit_operation_markers)
            leading_operation = any(compact.startswith(marker) for marker in edit_operation_markers)
            implicit_directed_edit = bool(
                directed
                and not re.search(r"(?:什么|怎么|为啥|为什么|吗|呢|？|\?)", compact)
                and any(
                    marker in compact
                    for marker in (
                        "红色",
                        "蓝色",
                        "绿色",
                        "黑色",
                        "白色",
                        "粉色",
                        "紫色",
                        "黄色",
                        "基调",
                        "色调",
                        "风格",
                        "背景",
                        "滤镜",
                        "清晰",
                        "高清",
                        "二次元",
                        "写实",
                        "赛博",
                    )
                )
            )
            edit_hit = bool(strong_edit or (operation_edit and (explicit_visual_target or leading_operation)) or implicit_directed_edit)
        if not draw_hit and not edit_hit:
            return {}
        prompt = raw
        cleanup_patterns = [
            r"^(?:麻烦|可以|能不能|能|帮我|给我|替我|请你|请)?",
            r"^(?:拍一张|拍张|拍个|拍一下|发一张|发张|来一张|来张)(?:自拍|照片|照|图片|图)?",
            r"^(?:用|走)?(?:这个|你|本)?(?:插件能力|插件|陪伴能力|陪伴插件)(?:来|去)?",
            r"^(?:重画|重绘|重新画|重新生成)(?:一张|一个|张|个)?(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:重新|再|再来|继续|重画|重绘)?(?:画一张|画张|画个|画一下|画一个|生成一张|生成一个|重新生成|再生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|画卷|图)?",
            r"^(?:画一张|画个|画一下|画一个|生成一张|生成一个|生成|生一张|做一张|做个|出一张|来一张|来张|整一张|整张|整一个|整个|画)(?:图片|照片|插画|头像|壁纸|表情包|图)?",
            r"^(?:把)?(?:这张图|这个图|这张|引用图|图片)?(?:帮我)?(?:改成|改为|改一下|改图|修图|重绘|p成|P成|换成|变成)",
        ]
        for pattern in cleanup_patterns:
            prompt = re.sub(pattern, "", prompt, count=1, flags=re.I).strip()
        prompt = prompt.strip(" ，,。.!！?？:：；;")
        if selfie_hit and prompt in {"", "看看", "看一下", "看看吧", "看看嘛"}:
            prompt = "拍一张自拍"
        if not prompt or prompt in {"图", "图片", "一张图", "这张", "这张图"}:
            return {
                "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
                "prompt": "",
                "needs_prompt": True,
            }
        return {
            "kind": "edit" if edit_hit else ("selfie" if selfie_hit else "text2img"),
            "prompt": _single_line(prompt, 700),
            "raw": raw,
        }

    def _natural_language_photo_quota_left(self, user: dict[str, Any]) -> int:
        limit = max(0, _safe_int(runtime_persona_setting(self, 'natural_language_photo_generation_max_daily', 0), 0))
        if limit <= 0:
            return 0
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        used = _safe_int(user.get("natural_photo_generated_today"), 0)
        if str(user.get("natural_photo_generated_day") or "") != today:
            used = 0
        return max(0, limit - used)

    def _note_natural_language_photo_generation_attempt(self, user: dict[str, Any], image_path: str = "") -> None:
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        if user.get("natural_photo_generated_day") != today:
            user["natural_photo_generated_day"] = today
            user["natural_photo_generated_today"] = 0
        user["natural_photo_generated_today"] = _safe_int(user.get("natural_photo_generated_today"), 0) + 1
        user["last_natural_photo_path"] = _path_text(image_path, 1000)
        user["last_natural_photo_at"] = _now_ts()

    def _command_photo_generation_daily_limit(self) -> int:
        return _safe_int(
            runtime_persona_setting(self, 'command_photo_generation_max_daily', -1),
            -1,
            -1,
            100,
        )

    def _command_photo_quota_block_message(self) -> str:
        if self._command_photo_generation_daily_limit() == 0:
            return "管理员已关闭用户请求生图/改图（“用户请求生图每日上限”为 0）。"
        return "今天用户请求生图/改图额度用完了。管理员可调高“用户请求生图每日上限”，或设为 -1 取消每日限制。"

    def _command_photo_quota_left(self, user: dict[str, Any]) -> int | None:
        limit = self._command_photo_generation_daily_limit()
        if limit < 0:
            return None
        if limit == 0:
            return 0
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        used = _safe_int(user.get("command_photo_generated_today"), 0)
        if str(user.get("command_photo_generated_day") or "") != today:
            used = 0
        return max(0, limit - used)

    def _note_command_photo_generation_attempt(self, user: dict[str, Any], image_path: str = "") -> None:
        today = self._environment_now().strftime("%Y-%m-%d") if callable(getattr(self, "_environment_now", None)) else ""
        if not today:
            today = str(getattr(self, "_today_key", lambda: "")() or "")
        if user.get("command_photo_generated_day") != today:
            user["command_photo_generated_day"] = today
            user["command_photo_generated_today"] = 0
        user["command_photo_generated_today"] = _safe_int(user.get("command_photo_generated_today"), 0) + 1
        user["last_command_photo_path"] = _path_text(image_path, 1000)
        user["last_command_photo_at"] = _now_ts()

    @staticmethod
    def _natural_photo_prompt_has_explicit_people_request(prompt: Any) -> bool:
        text = _single_line(prompt, 1200).lower()
        if not text:
            return False
        if any(
            marker in text
            for marker in (
                "人物",
                "角色",
                "女孩",
                "少女",
                "女人",
                "男人",
                "男生",
                "女生",
                "男孩",
                "小孩",
                "人群",
                "路人",
                "游客",
                "行人",
                "背影",
            )
        ):
            return True
        return bool(
            re.search(
                r"\b(?:person|people|girl|woman|boy|man|human|character|crowd|pedestrian|tourist)s?\b",
                text,
                flags=re.I,
            )
        )

    @staticmethod
    def _natural_photo_prompt_has_explicit_back_view_request(prompt: Any) -> bool:
        text = _single_line(prompt, 1200).lower()
        if not text:
            return False
        return bool(
            any(marker in text for marker in ("背影", "背对镜头", "背对相机", "从背后", "身后视角"))
            or re.search(r"\b(?:back[-\s]?view|from\s+behind|facing\s+away)\b", text, flags=re.I)
        )

    def _build_natural_language_photo_prompt(
        self,
        *,
        prompt: str,
        kind: str,
        has_reference: bool,
        memory_context: str = "",
        structured: bool = False,
    ) -> str | tuple[PhotoPromptSection, ...]:
        style_name, style_instruction = self._get_photo_style_instruction() if callable(getattr(self, "_get_photo_style_instruction", None)) else ("默认", "")
        style_prompt = (
            self._photo_style_prompt_en(style_name, style_instruction)
            if callable(getattr(self, "_photo_style_prompt_en", None))
            else (_single_line(style_instruction, 220) or _single_line(style_name, 40) or "natural image style")
        )
        extra_prompt = str(
            runtime_persona_setting(self, 'natural_language_photo_extra_prompt', DEFAULT_NATURAL_LANGUAGE_PHOTO_EXTRA_PROMPT)
            or ""
        ).strip()
        visual_memory = self._visual_photo_memory_context(memory_context)
        explicit_people_request = self._natural_photo_prompt_has_explicit_people_request(prompt)
        explicit_back_view_request = self._natural_photo_prompt_has_explicit_back_view_request(prompt)
        referenced_group_request = bool(has_reference and _photo_group_request_matches(prompt))
        if kind not in {"edit", "selfie"} and style_name == "二次元" and not explicit_people_request:
            style_prompt = (
                "2D anime illustration style, detailed environment and object art, "
                "cel-shaded rendering, soft colors, slice-of-life atmosphere"
            )
        if kind == "edit" and has_reference:
            user_request = _single_line(prompt, 420) or "edit the reference image"
            positive = [
                "image edit based on the provided reference image",
                "the provided image is the sole visual reference and source canvas",
                "this is an image editing task, not a selfie or new portrait generation request",
                "preserve unchanged subjects, composition, identity, clothing, and important details",
                "only modify the parts explicitly requested by the user",
                "do not replace any person with the assistant persona or today's outfit",
                style_prompt,
            ]
            negative = [
                "unrequested identity change",
                "unrequested outfit change",
                "changed composition",
                "extra people",
                "text",
                "watermark",
                "logo",
                "nsfw",
            ]
        elif kind == "selfie":
            user_request = _single_line(prompt, 420) or "take a selfie"
            if referenced_group_request:
                identity_continuity = (
                    "preserve every referenced person's identity, count, relative placement, and stable appearance from the explicitly supplied source image"
                )
                positive = [
                    "multi-person photo based only on the explicitly supplied source reference",
                    "preserve every referenced person and do not invent anyone else",
                    "one continuous scene",
                    "natural expressions and interaction",
                    "clear faces when visible in the source",
                    "natural snapshot composition",
                    "soft natural light",
                    style_prompt,
                ]
            elif explicit_back_view_request:
                identity_continuity = (
                    "preserve character identity and stable appearance from the selected reference image"
                    if has_reference
                    else "preserve character identity and stable appearance from available visual continuity"
                )
                positive = [
                    "single character environmental portrait",
                    "solo",
                    "the requested back view is intentional",
                    "complete head and hair",
                    "recognizable hairstyle silhouette and stable character appearance",
                    "outfit and surrounding scene visible",
                    "natural environmental composition",
                    "soft natural light",
                    style_prompt,
                ]
            else:
                identity_continuity = (
                    "preserve character identity and stable appearance from the selected reference image"
                    if has_reference
                    else "preserve character identity and stable appearance from available visual continuity"
                )
                positive = [
                    "single character selfie",
                    "solo",
                    "visible face",
                    "complete head and hair",
                    "clear eyes",
                    "natural expression",
                    "upper body or outfit visible",
                    "natural phone snapshot",
                    "centered composition",
                    "soft natural light",
                    style_prompt,
                ]
            negative = [
                "cropped head",
                "headless",
                "body only",
                "outfit only",
                "bad hands",
                "extra fingers",
                "text",
                "watermark",
                "logo",
                "unreferenced extra people" if referenced_group_request else "other people",
                "nsfw",
            ]
            if not explicit_back_view_request and not referenced_group_request:
                negative.extend(["faceless", "face hidden", "back view"])
        else:
            user_request = _single_line(prompt, 520)
            positive = [
                "generate an image from the user request",
                "clear main subject",
                "concrete scene",
                "natural lighting",
                "clean composition",
                "no private screen",
                style_prompt,
            ]
            negative = [
                "vague empty scene",
                "unrelated subject",
                "private information",
                "text",
                "watermark",
                "logo",
                "nsfw",
            ]
            if not explicit_people_request:
                positive.append(
                    "show only the requested scene or object; do not add any unrequested person, character, or visible photographer"
                )
                negative.extend(
                    [
                        "unrequested person",
                        "unrequested character",
                        "visible photographer",
                        "back-view person",
                    ]
                )
        sections = [
            PhotoPromptSection(
                name="user_request",
                source="user_request",
                positive=f"user request: {user_request}",
                protected=True,
            )
        ]
        sections.append(
            PhotoPromptSection(
                name="natural_language_contract",
                source="edit_contract" if kind == "edit" and has_reference else "composition",
                positive=", ".join(part for part in positive if _single_line(part, 520)),
                negative=", ".join(negative),
            )
        )
        if kind == "selfie":
            sections.append(
                PhotoPromptSection(
                    name="identity_continuity",
                    source="visual_memory",
                    positive=identity_continuity,
                )
            )
        if visual_memory and kind != "edit":
            sections.append(
                PhotoPromptSection(
                    name="visual_memory",
                    source="visual_memory",
                    positive=f"visual continuity reference: {_single_line(visual_memory, 360)}",
                )
            )
        if extra_prompt:
            sections.append(
                PhotoPromptSection(
                    name="natural_language_extra",
                    source="fixed_prompt",
                    positive=f"additional generation preference: {_single_line(extra_prompt, 420)}",
                )
            )
        if structured:
            return tuple(sections)
        combined_positive = ", ".join(section.positive for section in sections if section.positive)
        combined_negative = ", ".join(section.negative for section in sections if section.negative)
        return _single_line(
            "Positive prompt: "
            + combined_positive
            + ". Negative prompt: "
            + combined_negative
            + ".",
            6500,
        )

    @staticmethod
    def _photo_generation_workflow_kind(intent_kind: str) -> str:
        normalized = str(intent_kind or "").strip().lower()
        if normalized in {"edit", "改图", "修图", "重绘", "p图"}:
            return "edit"
        if normalized in {"selfie", "portrait", "自拍", "人像", "sticker", "emoji", "meme", "表情包", "贴纸"}:
            return "selfie"
        return "text2img"

    def _visual_photo_memory_context(self, memory_context: str, *, limit: int = 520) -> str:
        raw = str(memory_context or "").strip()
        if not raw:
            return ""
        raw = re.sub(r"<instruction\b[^>]*>.*?(?:</instruction>|$)", " ", raw, flags=re.I | re.S)
        raw = re.sub(r"</?(?:MemoryCompanion-Context|memory_companion_context)\b[^>]*>", " ", raw, flags=re.I)
        raw = re.sub(r"<[^>\n]{0,120}>", " ", raw)
        raw = raw.replace("RememberYou", "我会牢牢记住你")
        reject_tokens = (
            "MemoryCompanion",
            "memory_companion",
            "instruction",
            "固定分工",
            "persona_memory",
            "open_loops",
            "promise",
            "relationship",
            "emotional",
            "facts",
            "不是用户新发言",
            "不是新的回复任务",
            "先回应",
            "不要让旧话题",
            "按 ",
            "分区理解",
            "只影响语气",
            "不确定内容",
            "必须带不确定",
            "需要避免",
            "用户常问",
            "今日穿搭生成",
            "历史穿搭",
        )
        visual_tokens = (
            "穿搭",
            "衣",
            "裙",
            "外套",
            "上衣",
            "裤",
            "鞋",
            "袜",
            "配饰",
            "发夹",
            "发型",
            "发色",
            "头发",
            "瞳色",
            "眼睛",
            "表情",
            "脸",
            "自拍",
            "照片",
            "参考图",
            "颜色",
            "色调",
            "风格",
            "背景",
            "地点",
            "位置",
            "室内",
            "室外",
            "家里",
            "学校",
            "咖啡",
            "房间",
            "街",
            "公园",
            "天气",
        )
        parts = re.split(r"[\n\r。；;|｜]+", raw)
        kept: list[str] = []
        for part in parts:
            item = _single_line(part, 120)
            if not item:
                continue
            if any(token in item for token in reject_tokens):
                continue
            if not any(token in item for token in visual_tokens):
                continue
            item = re.sub(r"^(?:[-*·•]\s*|\d+[.、]\s*)", "", item).strip()
            item = re.sub(r"^(?:我会牢牢记住你|RememberYou|MemoryCompanion)\s*(?:相关)?(?:记忆|参考)?[：:]\s*", "", item, flags=re.I).strip()
            item = re.sub(r"^(?:记忆|相关记忆|参考|内容|摘要)[：:]\s*", "", item).strip()
            if item and item not in kept:
                kept.append(_single_line(item, 90))
            if len(kept) >= 5:
                break
        return _single_line("；".join(kept), limit)

    def _natural_language_photo_ack_text(self, *, kind: str, has_reference: bool) -> str:
        return "等我一下。"

    def _natural_language_photo_done_text(self, *, kind: str, reference_label: str = "") -> str:
        return "好了，你看。"

    def _natural_language_photo_ack_reference(self, *, kind: str, has_reference: bool) -> str:
        if kind == "edit" or has_reference:
            return "参考意图：已经拿到参考图，开始按用户要求改图；让用户稍等，不要描述工具执行过程。"
        if kind == "selfie":
            return "参考意图：用户要角色自拍，先轻轻回应会去准备；让用户稍等，不要承诺额外内容。"
        return "参考意图：用户要生成图片，先轻轻回应会去画；让用户稍等，不要描述工具执行过程。"

    def _natural_language_photo_done_reference(self, *, kind: str, reference_label: str = "") -> str:
        if kind == "edit":
            label = _single_line(reference_label, 24) or "这张图"
            return f"参考意图：图片已按{label}改好，提醒用户看图；语气自然短一点。"
        if kind == "selfie":
            return "参考意图：自拍图片已经完成，提醒用户看图；语气自然短一点。"
        return "参考意图：图片已经完成，提醒用户看图；语气自然短一点。"

    async def _natural_language_photo_ack_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        has_reference: bool,
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_ack_reference(kind=kind, has_reference=has_reference),
                scene="规则快判生图/改图已接单，生成前短回执",
                user=user,
                event=event,
                fallback_text="等我一下。",
                task="natural_photo_ack_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "等我一下。"

    async def _natural_language_photo_done_reply_text(
        self,
        event: AstrMessageEvent,
        user: dict[str, Any],
        *,
        kind: str,
        reference_label: str = "",
    ) -> str:
        rewriter = getattr(self, "_rewrite_reference_reply_with_persona", None)
        if callable(rewriter):
            text = await rewriter(
                self._natural_language_photo_done_reference(kind=kind, reference_label=reference_label),
                scene="规则快判生图/改图已完成，随图短标题",
                user=user,
                event=event,
                fallback_text="好了，你看。",
                task="natural_photo_done_rewrite",
                max_chars=60,
                allow_fallback=True,
                preserve_status=True,
            )
            if text:
                return text
        return "好了，你看。"

    async def _maybe_handle_natural_language_photo_request(
        self,
        event: AstrMessageEvent,
        user_id: str,
        text: str,
        *,
        directed: bool = False,
    ) -> bool:
        text = _single_line(text, 800)
        if not text or text.startswith(("陪伴", "/陪伴", "私聊陪伴", "主动陪伴")):
            return False
        explicit_plugin_request = self._natural_language_photo_explicit_plugin_request(text)
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
                event.stop_event()
                return True
            return False
        mode = _single_line(runtime_persona_setting(self, 'natural_language_photo_generation_mode', "tool_first"), 40).lower()
        if mode not in {"tool_first", "rule_fast", "off"}:
            mode = "tool_first"
        if mode in {"tool_first", "off"}:
            if explicit_plugin_request:
                logger.info(
                    "[PrivateCompanion] 非指令生图交给主链工具处理: mode=%s user=%s text=%s",
                    mode,
                    _single_line(user_id, 40),
                    _single_line(text, 160),
                )
            return False
        if not runtime_persona_setting(self, 'enable_natural_language_photo_generation', False):
            if explicit_plugin_request:
                await self._reply(event, self._natural_language_photo_disabled_text("natural_off"))
                event.stop_event()
                return True
            return False
        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_intent"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 自然语言生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False
        intent = self._natural_language_photo_intent(text, has_reference=has_reference, directed=directed)
        if not intent:
            if directed:
                logger.info(
                    "[PrivateCompanion] 定向自然语言生图未命中意图: user=%s has_reference=%s text=%s",
                    _single_line(user_id, 40),
                    has_reference,
                    _single_line(text, 180),
                )
            return False
        group_photo_requested = _photo_group_request_matches(
            intent.get("prompt") or text
        )
        if group_photo_requested and intent.get("kind") != "edit":
            intent["kind"] = "selfie"
        logger.info(
            "[PrivateCompanion] 自然语言生图命中: user=%s kind=%s has_reference=%s prompt=%s raw=%s",
            _single_line(user_id, 40),
            _single_line(intent.get("kind"), 30),
            has_reference,
            _single_line(intent.get("prompt"), 180),
            _single_line(text, 180),
        )
        if intent.get("needs_prompt"):
            await self._reply(event, "要画成什么样？给我一句具体点的描述就行。")
            event.stop_event()
            return True
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        photo_scope = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            scope_getter = getattr(self, "_photo_generation_scope", None)
            if callable(scope_getter):
                photo_scope = scope_getter(event, user=user, user_id=user_id)
            scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
            scope_left = (
                scope_quota_getter(
                    event,
                    user=user,
                    user_id=user_id,
                    scope=photo_scope,
                )
                if callable(scope_quota_getter)
                else None
            )
            scope_blocked = scope_left is not None and scope_left <= 0
            if not callable(scope_quota_getter) and callable(scope_checker):
                scope_blocked = not scope_checker(event, user=user, user_id=user_id)
            if scope_blocked:
                message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
                message = (
                    message_getter(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                    if callable(message_getter)
                    else "当前会话不允许生图/改图，或今天该范围的生图额度已经用完。"
                )
                await self._reply(event, message)
                event.stop_event()
                return True
            if self._natural_language_photo_quota_left(user) <= 0:
                await self._reply(event, "今天规则快判生图/改图额度用完了。")
                event.stop_event()
                return True
        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True
        reference_path = ""
        reference_label = ""
        reference_kind = str(intent.get("kind") or "")
        reference_required = reference_kind == "edit" or group_photo_requested
        if reference_required or reference_kind == "selfie":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 自然语言生图引用来源解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                reference_usage = (
                    "合影"
                    if group_photo_requested
                    else ("改图" if reference_kind == "edit" else "自拍")
                )
                await self._reply(
                    event,
                    f"{reference_usage}参考图解析缺少可选依赖 {missing}，这次先不生成。",
                )
                event.stop_event()
                return True
            logger.info(
                "[PrivateCompanion] 自然语言生图引用来源解析: user=%s kind=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                _single_line(reference_kind, 30),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path and (reference_required or saw_image):
                await self._reply(
                    event,
                    (
                        "合影需要人物参考图。请把合影原图或包含相关人物的参考图和要求一起发，或引用图片后再说合影要求。"
                        if group_photo_requested
                        else "我没拿到要改的图。可以把图片和要求一起发，或者引用一张近期图片再说“改成……”。"
                    )
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时无法生成。",
                )
                event.stop_event()
                return True
        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="natural_photo",
                    query=(
                        f"自然语言生图 {intent.get('kind') or ''} {intent.get('prompt') or ''} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""
        prompt_sections = self._build_natural_language_photo_prompt(
            prompt=str(intent.get("prompt") or ""),
            kind=str(intent.get("kind") or "text2img"),
            has_reference=bool(reference_path),
            memory_context=memory_context,
            structured=True,
        )
        prompt_text = str(intent.get("prompt") or "")
        intent_kind = str(intent.get("kind") or "text2img")
        workflow_kind = self._photo_generation_workflow_kind(intent_kind)
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user,
            kind=intent_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        generation_session_key = f"natural_photo_{user_id}"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(getattr(event, "unified_msg_origin", ""), user_id)
            if callable(continuity_composer)
            else ""
        )
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                request_text=str(intent.get("prompt") or ""),
                session_key=generation_session_key,
                continuity_key=continuity_key,
                requester_user_id=user_id,
                requester_is_private=bool(
                    (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
                ),
                reference_image_path=reference_path,
                prompt_sections=prompt_sections,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 自然语言生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "[PrivateCompanion] 自然语言生图结果: user=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_natural_language_photo_generation_attempt(user, image_path=image_path)
                scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                if callable(scope_notifier):
                    scope_notifier(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                self._save_data_sync(sections={"users", "photo_generation_scope_attempts"})
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入规则快判生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user,
            kind=intent_kind,
            reference_label=reference_label,
        )
        metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
        generation_metadata = (
            metadata_getter(image_path=image_path, session_key=generation_session_key)
            if callable(metadata_getter)
            else {}
        )
        fallback_payload = (
            generation_metadata.get("reference_fallback")
            if isinstance(generation_metadata, dict)
            else {}
        )
        fallback_message = _single_line(
            fallback_payload.get("message") if isinstance(fallback_payload, dict) else "",
            260,
        )
        if fallback_message:
            caption = f"{caption}\n{fallback_message}".strip()
        delivery = await self._deliver_generated_image_to_event(
            event,
            image_path=image_path,
            caption=caption,
        )
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="natural_photo_rule",
                intent_kind=intent_kind,
                sent=bool(delivery.get("sent")),
                caption=caption,
                tool_name="natural_photo_rule",
            )
        if not delivery.get("sent"):
            await self._reply(event, _single_line(delivery.get("message"), 180) or "图片未能发送。")
        elif delivery.get("destination") == "private":
            await self._reply(event, "图片已私聊发送。")
        event.stop_event()
        return True

    async def _handle_companion_photo_command(
        self,
        event: AstrMessageEvent,
        user_id: str,
        action: str,
        value: str,
    ) -> bool:
        """Run the plugin image backend from an explicit /陪伴 command."""
        action_text = _single_line(action, 24)
        prompt = _single_line(value, 800).strip()
        action_kind_map = {
            "自拍": "selfie",
            "拍照": "selfie",
            "拍一张": "selfie",
            "改图": "edit",
            "修图": "edit",
            "重绘": "edit",
            "P图": "edit",
            "p图": "edit",
        }
        forced_kind = action_kind_map.get(action_text, "text2img")
        if not runtime_persona_setting(self, 'enable_photo_text_action', False):
            await self._reply(event, self._natural_language_photo_disabled_text("photo_off"))
            event.stop_event()
            return True
        scope_checker = getattr(self, "_photo_generation_scope_allowed", None)
        try:
            safe_has_image = getattr(self, "_private_event_has_image_safe", None)
            if callable(safe_has_image):
                has_reference = bool(safe_has_image(event, label="natural_photo_quota"))
            else:
                has_reference_checker = getattr(self, "_private_event_has_image", None)
                has_reference = bool(has_reference_checker(event) if callable(has_reference_checker) else False)
            has_reference = has_reference or bool(self._photo_reference_sources_from_reply_cache(event))
            if not has_reference:
                has_reference = bool(await self._photo_reference_sources_from_reply_event(event))
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 指令生图参考图检测缺少可选模型依赖，已按无参考图继续: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            has_reference = False

        compact = re.sub(r"\s+", "", prompt)
        group_photo_requested = _photo_group_request_matches(prompt)
        if forced_kind == "text2img":
            if group_photo_requested or any(marker in compact for marker in ("自拍", "拍照", "拍张照", "拍一张照", "来张自拍", "发张自拍")):
                forced_kind = "selfie"
            elif has_reference and any(marker in compact for marker in ("改图", "修图", "重绘", "p图", "P图", "改成", "改为", "换成", "变成", "加上", "去掉", "去除")):
                forced_kind = "edit"

        if forced_kind == "selfie" and not prompt:
            prompt = "拍一张自拍"
        if not prompt:
            usage = (
                "请这样使用：\n"
                "陪伴 生图 <画面描述>\n"
                "陪伴 自拍 [画面要求]\n"
                "陪伴 改图 <修改要求>（需要带图或回复图片）"
            )
            await self._reply(event, usage)
            event.stop_event()
            return True

        photo_scope = ""
        async with self._data_lock:
            user = self._get_user(user_id)
            scope_getter = getattr(self, "_photo_generation_scope", None)
            if callable(scope_getter):
                photo_scope = scope_getter(event, user=user, user_id=user_id)
            scope_quota_getter = getattr(self, "_photo_generation_scope_quota_left", None)
            scope_left = (
                scope_quota_getter(
                    event,
                    user=user,
                    user_id=user_id,
                    scope=photo_scope,
                )
                if callable(scope_quota_getter)
                else None
            )
            scope_blocked = scope_left is not None and scope_left <= 0
            if not callable(scope_quota_getter) and callable(scope_checker):
                scope_blocked = not scope_checker(event, user=user, user_id=user_id)
            if scope_blocked:
                message_getter = getattr(self, "_photo_generation_scope_quota_block_message", None)
                message = (
                    message_getter(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                    if callable(message_getter)
                    else "当前会话不允许生图/改图，或今天该范围的生图额度已经用完。"
                )
                await self._reply(event, message)
                event.stop_event()
                return True
            quota_left = self._command_photo_quota_left(user)
            if quota_left is not None and quota_left <= 0:
                await self._reply(event, self._command_photo_quota_block_message())
                event.stop_event()
                return True

        if not self._photo_text_available():
            await self._reply(event, "现在没有可用的生图后端，先画不了。")
            event.stop_event()
            return True

        reference_path = ""
        reference_label = ""
        reference_required = forced_kind == "edit" or group_photo_requested
        if reference_required or forced_kind == "selfie":
            try:
                reference_path, reference_label, saw_image = await self._photo_reference_image_from_command_context(event, user_id)
            except Exception as exc:
                missing = _missing_optional_model_dependency(exc)
                if not missing:
                    raise
                logger.warning(
                    "[PrivateCompanion] 指令生图引用来源解析缺少可选模型依赖: module=%s err=%s",
                    missing,
                    _single_line(exc, 160),
                )
                reference_usage = (
                    "合影"
                    if group_photo_requested
                    else ("改图" if forced_kind == "edit" else "自拍")
                )
                await self._reply(
                    event,
                    f"{reference_usage}参考图解析缺少可选依赖 {missing}，这次先不生成。",
                )
                event.stop_event()
                return True
            logger.info(
                "[PrivateCompanion] 指令生图引用来源解析: user=%s kind=%s saw_image=%s label=%s path=%s exists=%s",
                _single_line(user_id, 40),
                _single_line(forced_kind, 30),
                saw_image,
                _single_line(reference_label, 40),
                _single_line(reference_path, 180),
                bool(reference_path and Path(reference_path).exists()),
            )
            if not reference_path and (reference_required or saw_image):
                await self._reply(
                    event,
                    (
                        "合影需要人物参考图。请把合影原图或包含相关人物的参考图和指令一起发，或引用图片后再试。"
                        if group_photo_requested
                        else "我没拿到要改的图。可以把图片和“陪伴 改图 <要求>”一起发，或者引用近期图片再用这个指令。"
                    )
                    if not saw_image
                    else "看到了图片，但没能保存成可用参考图，暂时无法生成。",
                )
                event.stop_event()
                return True

        memory_context = ""
        memory_getter = getattr(self, "_memory_companion_compose_feature_context", None)
        if callable(memory_getter):
            try:
                memory_context = await memory_getter(
                    kind="command_photo",
                    query=(
                        f"指令生图 {forced_kind} {prompt} "
                        "今日穿搭 当前地点 当前日程 最近自拍 用户偏好 衣服颜色"
                    ),
                    event=event,
                    user_id=user_id,
                    top_k=5,
                    max_chars=760,
                    timeout_seconds=1.5,
                )
            except Exception:
                memory_context = ""

        prompt_sections = self._build_natural_language_photo_prompt(
            prompt=prompt,
            kind=forced_kind,
            has_reference=bool(reference_path),
            memory_context=memory_context,
            structured=True,
        )
        prompt_text = prompt
        workflow_kind = self._photo_generation_workflow_kind(forced_kind)
        async with self._data_lock:
            user = self._get_user(user_id)
            user_snapshot = dict(user)
        ack_text = await self._natural_language_photo_ack_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            has_reference=bool(reference_path),
        )
        await self._reply(event, ack_text)
        generation_session_key = f"command_photo_{user_id}"
        continuity_composer = getattr(self, "_compose_photo_continuity_key", None)
        continuity_key = (
            continuity_composer(getattr(event, "unified_msg_origin", ""), user_id)
            if callable(continuity_composer)
            else ""
        )
        try:
            backend_name, image_path, note = await self._generate_photo_image(
                workflow_kind=workflow_kind,
                prompt_text=prompt_text,
                request_text=prompt,
                session_key=generation_session_key,
                continuity_key=continuity_key,
                requester_user_id=user_id,
                requester_is_private=bool(
                    (getattr(event, "is_private_chat", lambda: False)() if callable(getattr(event, "is_private_chat", None)) else getattr(event, "is_private_chat", False))
                ),
                reference_image_path=reference_path,
                prompt_sections=prompt_sections,
            )
        except Exception as exc:
            missing = _missing_optional_model_dependency(exc)
            if not missing:
                raise
            logger.warning(
                "[PrivateCompanion] 指令生图后端缺少可选模型依赖: module=%s err=%s",
                missing,
                _single_line(exc, 160),
            )
            await self._reply(event, f"生图后端缺少可选依赖 {missing}，这次先不生成。")
            event.stop_event()
            return True
        logger.info(
            "[PrivateCompanion] 指令生图结果: user=%s action=%s backend=%s ok=%s note=%s image=%s",
            _single_line(user_id, 40),
            action_text,
            _single_line(backend_name, 80),
            bool(image_path),
            _single_line(note, 180),
            _single_line(image_path, 180),
        )
        counted = bool(image_path)
        if not image_path and callable(getattr(self, "_photo_generation_failure_counts_as_attempt", None)):
            counted = bool(self._photo_generation_failure_counts_as_attempt(note))
        if counted:
            async with self._data_lock:
                user = self._get_user(user_id)
                self._note_command_photo_generation_attempt(user, image_path=image_path)
                scope_notifier = getattr(self, "_note_photo_generation_scope_attempt", None)
                if callable(scope_notifier):
                    scope_notifier(
                        event,
                        user=user,
                        user_id=user_id,
                        scope=photo_scope,
                    )
                self._save_data_sync(sections={"users", "photo_generation_scope_attempts"})
        if not image_path:
            await self._reply(
                event,
                f"这次没生成出来：{_single_line(note, 160) or '后端没有返回图片'}"
                + ("\n这次已经计入今日指令生图额度，避免后端异常时反复请求。" if counted else ""),
            )
            event.stop_event()
            return True
        caption = await self._natural_language_photo_done_reply_text(
            event,
            user_snapshot,
            kind=forced_kind,
            reference_label=reference_label,
        )
        metadata_getter = getattr(self, "_photo_generation_result_metadata", None)
        generation_metadata = (
            metadata_getter(image_path=image_path, session_key=generation_session_key)
            if callable(metadata_getter)
            else {}
        )
        fallback_payload = (
            generation_metadata.get("reference_fallback")
            if isinstance(generation_metadata, dict)
            else {}
        )
        fallback_message = _single_line(
            fallback_payload.get("message") if isinstance(fallback_payload, dict) else "",
            260,
        )
        if fallback_message:
            caption = f"{caption}\n{fallback_message}".strip()
        delivery = await self._deliver_generated_image_to_event(
            event,
            image_path=image_path,
            caption=caption,
        )
        annotator = getattr(self, "_annotate_recent_photo_generation", None)
        if callable(annotator):
            annotator(
                image_path=image_path,
                session_key=generation_session_key,
                trigger="command_photo",
                intent_kind=forced_kind,
                sent=bool(delivery.get("sent")),
                caption=caption,
                tool_name="companion_photo_command",
            )
        if not delivery.get("sent"):
            await self._reply(event, _single_line(delivery.get("message"), 180) or "图片未能发送。")
        elif delivery.get("destination") == "private":
            await self._reply(event, "图片已私聊发送。")
        event.stop_event()
        return True

    async def _group_companion_command_impl(self, event: AstrMessageEvent):
        group_id = self._extract_group_id_from_event(event)
        if not group_id:
            yield event.plain_result("这条命令需要在群聊里使用。")
            return
        message = str(event.message_str or "").strip()
        action = ""
        response_chain = None
        parts = message.split(maxsplit=2)
        if len(parts) >= 2:
            action = parts[1].strip()
        value = parts[2].strip() if len(parts) >= 3 else ""
        action_compact = re.sub(r"\s+", "", f"{action}{value}").lower()
        llm_block_on = action_compact in {
            "关闭llm",
            "关闭llm回复",
            "关闭所有llm回复",
            "禁用llm",
            "禁用llm回复",
            "停用llm",
            "停用llm回复",
            "禁止llm",
            "禁止llm回复",
            "关闭主链",
            "关闭主链回复",
        }
        llm_block_off = action_compact in {
            "开启llm",
            "开启llm回复",
            "开启所有llm回复",
            "启用llm",
            "启用llm回复",
            "打开llm",
            "打开llm回复",
            "恢复llm",
            "恢复llm回复",
            "恢复主链",
            "恢复主链回复",
        }
        llm_block_status = action_compact in {"llm状态", "主链状态", "llm回复状态"}
        if llm_block_on or llm_block_off or llm_block_status:
            authorized = self._can_manage_group_companion(event)
            if not authorized:
                # Some adapters omit sender.role. Refresh the group member snapshot
                # before denying a real group owner/admin whose cache has expired.
                refresher = getattr(self, "_refresh_group_role_snapshot", None)
                if callable(refresher):
                    try:
                        await refresher(event, group_id, force=False)
                    except Exception as exc:
                        logger.debug(
                            "[PrivateCompanion] 刷新群权限快照失败: group=%s error=%s",
                            _single_line(group_id, 80),
                            _single_line(exc, 160),
                        )
                    authorized = self._can_manage_group_companion(event)
            if not authorized:
                yield event.plain_result(self._management_denied_text())
                return
        if llm_block_on or llm_block_off or llm_block_status:
            operator_id = ""
            try:
                operator_id = str(event.get_sender_id())
            except Exception:
                operator_id = ""
            async with self._data_lock:
                if llm_block_on:
                    item = self._set_group_llm_reply_block(
                        group_id,
                        True,
                        operator_id=operator_id,
                        reason="group_command",
                    )
                    self._save_data_sync(sections={"group_llm_reply_blocks"})
                    ts_text = self._format_timestamp_elapsed(_safe_float(item.get("updated_at"), 0.0, 0.0)) if item else "刚刚"
                    response = (
                        "已关闭本群所有 LLM 回复。\n"
                        f"群号：{group_id}\n"
                        f"状态：拦截中（{ts_text}）\n"
                        "恢复：陪伴群 开启LLM"
                    )
                elif llm_block_off:
                    self._set_group_llm_reply_block(
                        group_id,
                        False,
                        operator_id=operator_id,
                        reason="group_command",
                    )
                    self._save_data_sync(sections={"group_llm_reply_blocks"})
                    response = "已恢复本群 LLM 回复。"
                else:
                    item = self._group_llm_reply_block_item(group_id)
                    if bool(item.get("enabled")):
                        ts_text = self._format_timestamp_elapsed(_safe_float(item.get("updated_at"), 0.0, 0.0))
                        response = f"本群 LLM 回复当前关闭中，开启时间：{ts_text}。\n恢复：陪伴群 开启LLM"
                    else:
                        response = "本群 LLM 回复当前未被单独关闭。"
            yield event.plain_result(response)
            event.stop_event()
            return
        if not runtime_persona_setting(self, 'enable_group_companion', True):
            yield event.plain_result(
                "群聊陪伴总开关当前关闭。\n"
                "这个群的名单和本群开关配置仍会保留，但暂时不会观察或参与群聊。\n"
                "请先在插件配置的群聊功能中开启“群聊陪伴”。"
            )
            return
        if not self._group_allowed_by_access_mode(group_id):
            if runtime_persona_setting(self, 'group_access_mode', 'whitelist') == "blacklist" and group_id in self._configured_group_blacklist_ids():
                yield event.plain_result("这个群在群聊陪伴黑名单中，暂时不启用。")
            elif runtime_persona_setting(self, 'group_access_mode', 'whitelist') == "whitelist":
                yield event.plain_result("这个群还没有加入群聊陪伴白名单，暂时不启用。")
            else:
                yield event.plain_result("这个群暂时不启用群聊陪伴。")
            return
        if action in {"开启", "启用", "打开", "关闭", "停用", "关掉", "撤回消息", "防撤回", "转述撤回", "撤回转述"} and not self._can_manage_group_companion(event):
            yield event.plain_result(self._management_denied_text())
            return
        async with self._data_lock:
            group = self._get_group(group_id)
            if action in {"开启", "启用", "打开"}:
                group["enabled"] = True
                self._save_data_sync(sections={"groups"})
                response = "群聊陪伴观察已开启。"
            elif action in {"关闭", "停用", "关掉"}:
                group["enabled"] = False
                self._save_data_sync(sections={"groups"})
                response = "群聊陪伴观察已关闭。"
            elif action in {"黑话", "梗", "词"}:
                slang = group.get("slang_terms") if isinstance(group.get("slang_terms"), list) else []
                meanings = group.get("slang_meanings") if isinstance(group.get("slang_meanings"), dict) else {}
                if slang:
                    lines = ["当前群内常见词/梗："]
                    for item in slang[:20]:
                        if not isinstance(item, dict):
                            continue
                        term = _single_line(item.get("term"), 20)
                        if not term:
                            continue
                        meaning = ""
                        if isinstance(meanings.get(term), dict):
                            meaning_item = meanings[term]
                            confidence = min(1.0, _safe_float(meaning_item.get("confidence"), 1.0, 0.0))
                            raw_meaning = _single_line(meaning_item.get("meaning"), 60)
                            raw_usage = _single_line(meaning_item.get("usage"), 60)
                            if confidence >= 0.55 and not self._is_uncertain_group_slang_meaning(raw_meaning, raw_usage):
                                meaning = raw_meaning
                        lines.append(f"- {term}｜出现 {item.get('count', 0)} 次" + (f"｜{meaning}" if meaning else ""))
                    response = "\n".join(lines)
                else:
                    response = "还没有学到稳定的群内常见词。"
            elif action in {"群友", "成员", "画像"}:
                members = group.get("members") if isinstance(group.get("members"), dict) else {}
                ranked = sorted(
                    [item for item in members.values() if isinstance(item, dict)],
                    key=lambda item: _safe_int(item.get("count"), 0, 0),
                    reverse=True,
                )[:12]
                if ranked:
                    response = "当前群内成员观察：\n" + "\n".join(
                        f"- {_single_line(item.get('name'), 18) or '群友'}"
                        + (
                            "｜" + " / ".join(
                                _single_line(x, 18)
                                for x in (item.get('recent_phrases') or [])[:3]
                                if _single_line(x, 18)
                            )
                            if item.get("recent_phrases")
                            else ""
                        )
                        for item in ranked
                    )
                else:
                    response = "还没有群友样本。"
            elif action in {"话题", "线程"}:
                response = "当前群聊话题线程：\n" + (self._format_group_topic_threads_for_prompt(group) or "暂无。")
            elif action in {"片段", "群聊片段", "记忆"}:
                response = "近期群聊片段记忆：\n" + (self._format_group_episodes_for_prompt(group) or "暂无。")
            elif action in {"插话判定", "插话反馈", "反馈"}:
                response = "群聊插话反馈：" + self._format_group_interjection_feedback(group)
            elif action in {"关系网", "关系网络", "互动关系"}:
                response = "群友互动图：\n" + (self._format_group_relationship_graph_for_prompt(group) or "暂无。")
            elif action in {"撤回消息", "防撤回", "转述撤回", "撤回转述"}:
                if not runtime_persona_setting(self, 'enable_recall_enhancement', True) or not runtime_persona_setting(self, 'enable_recall_transcribe_command', True):
                    response = "撤回消息转述没有开启。"
                else:
                    response = self._format_recalled_messages_for_event(event, limit=5)
                    extra_components = self._recalled_message_media_components_for_event(event, limit=5)
                    if extra_components:
                        response_chain = self._build_outbound_chain(response, extra_components=extra_components)
            elif action in {"状态", "气氛", ""}:
                response = self._format_group_status(group)
            else:
                response = (
                    "群聊陪伴命令：\n"
                    "陪伴群 状态\n"
                    "陪伴群 黑话\n"
                    "陪伴群 群友\n"
                    "陪伴群 话题\n"
                    "陪伴群 片段\n"
                    "陪伴群 插话反馈\n"
                    "陪伴群 关系网\n"
                    "陪伴群 撤回消息\n"
                    "陪伴群 LLM状态\n"
                    "陪伴群 关闭LLM\n"
                    "陪伴群 开启LLM\n"
                    "陪伴群 开启\n"
                    "陪伴群 关闭"
                )
        if response_chain:
            yield event.chain_result(response_chain)
        else:
            yield event.plain_result(response)
        event.stop_event()
