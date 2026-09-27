# -*- coding: utf-8 -*-
"""config_schema 域。

由 tools/split_mixin_domain.py 从 page_api.py 机械抽取（25 个方法 + 0 个模块级名字 + 0 个类级赋值 / 537 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApi）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from .config_migration import _ensure_config_parent_dir
from .helpers import _MISSING, _flat_get, _set_into_config
from copy import deepcopy
from pathlib import Path
from .page_api_shared import _page_api_host, _page_api_host_request as request
from typing import Any

from .logging_util import get_module_logger

logger = get_module_logger(__name__)



class PrivateCompanionPageApiConfigSchemaMixin:
    """config_schema 域（从 PrivateCompanionPageApi 拆出）。"""


    async def apply_preset(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        name = str(payload.get("name", "")).strip()
        presets = self._presets()
        if name not in presets:
            return self._error("未知预设")
        preset = presets[name]
        try:
            for key, value in preset.get("settings", {}).items():
                self._apply_config_value(key, self._normalize_setting_value(key, value))
            for key, value in preset.get("features", {}).items():
                if key in self._allowed_feature_keys():
                    self._apply_config_value(key, self._normalize_bool_value(value))
            if any(key in self._allowed_provider_keys() for key in preset.get("settings", {})) or "provider_config_mode" in preset.get("settings", {}):
                apply_quick = getattr(self.plugin, "_apply_quick_provider_defaults", None)
                if callable(apply_quick):
                    apply_quick()
            config_saved = await self._save_config_if_possible()
            overview = await self.get_overview()
            if overview.get("success"):
                overview["data"]["preset"] = name
                overview["data"]["preset_label"] = preset.get("label", name)
                overview["data"]["config_saved"] = config_saved
            return overview
        except Exception as exc:
            logger.error(f"应用预设失败: {exc}", exc_info=True)
            return self._exception_error("应用预设失败")

    async def update_external_ability(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        normalizer = getattr(self.plugin, "_normalize_external_ability_name", None)
        name = self._single_line(payload.get("name"), 80)
        name = normalizer(name) if callable(normalizer) else name
        if not name:
            return self._error("缺少外部能力名称")
        try:
            async with self.plugin._data_lock:
                store_getter = getattr(self.plugin, "_external_ability_store", None)
                store = store_getter() if callable(store_getter) else self.plugin.data.setdefault("external_proactive_abilities", {})
                if not isinstance(store, dict):
                    store = {}
                    self.plugin.data["external_proactive_abilities"] = store
                item = store.get(name) if isinstance(store.get(name), dict) else {"name": name}
                if "enabled" in payload:
                    item["enabled"] = bool(payload.get("enabled"))
                if "share_probability" in payload:
                    item["share_probability"] = max(0.0, min(1.0, self._float(payload.get("share_probability"))))
                if "min_interval_hours" in payload:
                    item["min_interval_hours"] = max(0.0, self._float(payload.get("min_interval_hours")))
                if "config" in payload:
                    config = payload.get("config")
                    if isinstance(config, str):
                        import json
                        config = json.loads(config or "{}")
                    if not isinstance(config, dict):
                        return self._error("自定义配置必须是 JSON 对象")
                    item["config"] = config
                item["updated_ts"] = time.time()
                store[name] = item
                self.plugin._save_data_sync(sections={"external_proactive_abilities"})
                data = deepcopy(self.plugin.data)
            return self._ok({"message": "已保存外部主动能力", "external_abilities": self._external_ability_summary(data)})
        except Exception as exc:
            logger.error(f"更新外部主动能力失败: {exc}", exc_info=True)
            return self._exception_error("更新外部主动能力失败")

    @staticmethod
    def _presets() -> dict[str, dict[str, Any]]:
        return {
            "safe": {
                "label": "保守低打扰",
                "settings": {
                    "max_daily_messages": 3,
                    "idle_minutes": 180,
                    "min_interval_minutes": 360,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 360,
                    "memory_refresh_interval_minutes": 720,
                    "episode_memory_refresh_messages": 12,
                    "episode_memory_refresh_minutes": 180,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                    "enable_livingmemory_integration": True,
                },
            },
            "standard": {
                "label": "标准陪伴",
                "settings": {
                    "max_daily_messages": 6,
                    "idle_minutes": 60,
                    "min_interval_minutes": 120,
                    "group_interject_max_daily": 1,
                    "group_interject_min_interval_minutes": 240,
                    "memory_refresh_interval_minutes": 360,
                    "episode_memory_refresh_messages": 8,
                    "episode_memory_refresh_minutes": 90,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_interjection": False,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                },
            },
            "active": {
                "label": "高互动学习",
                "settings": {
                    "max_daily_messages": 10,
                    "idle_minutes": 30,
                    "min_interval_minutes": 60,
                    "group_interject_max_daily": 2,
                    "group_interject_min_interval_minutes": 180,
                    "memory_refresh_interval_minutes": 240,
                    "episode_memory_refresh_messages": 5,
                    "episode_memory_refresh_minutes": 60,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_injection_guard": True,
                    "enable_companion_memory": True,
                    "enable_expression_learning": True,
                    "enable_intent_emotion_analysis": True,
                    "enable_passive_response_review": True,
                    "enable_proactive_message_review": True,
                    "enable_dialogue_episode_memory": True,
                    "enable_open_loop_tracking": True,
                    "enable_group_interjection": True,
                    "enable_group_interjection_feedback": True,
                },
            },
            "group_observer": {
                "label": "群聊观察优先",
                "settings": {
                    "max_daily_messages": 4,
                    "idle_minutes": 90,
                    "min_interval_minutes": 180,
                    "group_interject_max_daily": 0,
                    "group_interject_min_interval_minutes": 240,
                    "max_group_recent_messages": 120,
                    "max_group_slang_terms": 80,
                },
                "features": {
                    "enable_group_companion": True,
                    "enable_group_context_injection": True,
                    "enable_group_injection_guard": True,
                    "enable_group_slang_learning": True,
                    "enable_group_member_profiles": True,
                    "enable_group_topic_threads": True,
                    "enable_group_episode_memory": True,
                    "enable_group_slang_meanings": True,
                    "enable_group_relationship_graph": True,
                    "enable_group_privacy_guard": True,
                    "enable_group_interjection": False,
                },
            },
        }

    @staticmethod
    def _normalize_bool_value(value: Any) -> bool:
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "1", "yes", "y", "on", "enable", "enabled", "启用", "开启", "开", "是"}:
                return True
            if text in {"false", "0", "no", "n", "off", "disable", "disabled", "停用", "关闭", "关", "否", ""}:
                return False
        return bool(value)

    def _set_config_value(self, key: str, value: Any) -> None:
        config = getattr(self.plugin, "config", None)
        if config is None:
            return
        if key == "provider_config_mode":
            self._set_provider_config_mode_value(config, value)
            return
        if key == "proactive_intensity_preset":
            self._set_schema_compat_value(config, key, value)
            return
        if key in {"page_font_family", "page_theme"}:
            self._set_schema_compat_value(config, key, value)
            return
        if key == "allow_generate_photo_on_reaction_turns":
            # Keep the grouped schema entry and the hidden legacy flat key in
            # sync: the runtime reads the flat attribute injected by AstrBot,
            # while the visible config page renders the grouped entry.  A
            # one-sided write leaves a stale flat False that silently disables
            # the opt-in switch after a reload.
            self._set_schema_compat_value(config, key, value)
            return
        updated_existing = _set_into_config(config, key, value, allow_flat_fallback=False)
        updated_group = self._set_schema_group_config_value(config, key, value, create_group=True)
        if updated_existing or updated_group:
            return
        _set_into_config(config, key, value)
        return

    def _set_provider_config_mode_value(self, config: Any, value: Any) -> None:
        # Keep the visible schema group and the hidden legacy flat key in sync.
        # AstrBot validates unknown flat keys during plugin load, while older
        # page/API paths still read or write the flat name directly.
        self._set_schema_group_config_value(config, "provider_config_mode", value, create_group=True)
        _set_into_config(config, "provider_config_mode", value)

    def _set_schema_compat_value(self, config: Any, key: str, value: Any) -> None:
        self._set_schema_group_config_value(config, key, value, create_group=True)
        # Write the hidden legacy key explicitly at the top level.  A recursive
        # setter would find the grouped key created above and leave an existing
        # top-level compatibility value stale.
        if isinstance(config, dict):
            config[key] = value
            return
        try:
            config[key] = value
            return
        except (AttributeError, KeyError, TypeError):
            pass
        for attr in ("data", "config"):
            target = getattr(config, attr, None)
            if isinstance(target, dict):
                target[key] = value
                return

    def _set_schema_group_config_value(self, config: Any, key: str, value: Any, *, create_group: bool = False) -> bool:
        group_key = self._schema_group_for_key(key)
        if not group_key:
            return False

        def set_in_group(target: dict[str, Any]) -> bool:
            group = target.get(group_key)
            if isinstance(group, dict):
                group[key] = value
                return True
            if create_group:
                target[group_key] = {key: value}
                return True
            return False

        if isinstance(config, dict) and set_in_group(config):
            return True
        for attr in ("data", "config"):
            target = getattr(config, attr, None)
            if isinstance(target, dict) and set_in_group(target):
                return True
        return False

    def _config_overlay(self, overrides: dict[str, Any]) -> Any:
        base = getattr(self.plugin, "config", {}) or {}

        class _Overlay:
            def get(self, item: str, default: Any = None) -> Any:
                if item in overrides:
                    return overrides[item]
                return _flat_get(base, item, getattr(base, item, default))

        return _Overlay()

    def _config_get_raw(self, key: str, default: Any = None) -> Any:
        # An explicitly cleared value is still a value.  Treating ``""`` as
        # missing makes an old flat compatibility key (or a stale runtime
        # attribute) reappear after the user clears a grouped Provider field.
        config = getattr(self.plugin, "config", None)
        value = _flat_get(config, key, _MISSING)
        if value is not _MISSING and value is not None:
            return value
        data = getattr(config, "data", None)
        if isinstance(data, dict):
            value = _flat_get(data, key, _MISSING)
            if value is not _MISSING and value is not None:
                return value
        raw = getattr(config, "config", None)
        if isinstance(raw, dict):
            value = _flat_get(raw, key, _MISSING)
            if value is not _MISSING and value is not None:
                return value
        try:
            return getattr(config, key, default)
        except Exception:
            return default

    def _config_get(self, key: str) -> str:
        value = self._config_get_raw(key, "")
        if isinstance(value, (list, dict)):
            try:
                return json.dumps(value, ensure_ascii=False)
            except Exception:
                return str(value)
        return str(value or "")

    def _private_alias_config_text(self, key: str) -> str:
        raw = self._config_get(key)
        if raw:
            return raw
        mapping = getattr(self.plugin, key, None)
        if not isinstance(mapping, dict):
            return ""
        lines: list[str] = []
        for alias, target in mapping.items():
            left = str(alias or "").strip()
            right = str(target or "").strip()
            if left and right:
                lines.append(f"{left}={right}")
        return "\n".join(lines)

    async def _save_config_if_possible(self) -> bool:
        config = getattr(self.plugin, "config", None)
        for method_name in ("save_config", "save", "save_conf"):
            save = getattr(config, method_name, None)
            if callable(save):
                try:
                    _ensure_config_parent_dir(config, logger=logger)
                    result = save()
                    if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                        result = await result
                    # Config adapters conventionally return None on success,
                    # but some expose an explicit False failure result.
                    if result is False:
                        logger.warning("配置保存失败(%s): 保存方法返回 False", method_name)
                        return False
                    return True
                except TypeError:
                    continue
                except FileNotFoundError as exc:
                    if _ensure_config_parent_dir(config, error=exc, logger=logger):
                        try:
                            result = save()
                            if asyncio.iscoroutine(result) or hasattr(result, "__await__"):
                                result = await result
                            if result is False:
                                logger.warning("配置保存重试失败(%s): 保存方法返回 False", method_name)
                                return False
                            return True
                        except Exception as retry_exc:
                            logger.warning("配置保存重试失败(%s): %s", method_name, self._single_line(retry_exc, 160))
                            return False
                    logger.warning("配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
                except Exception as exc:
                    logger.warning("配置保存失败(%s): %s", method_name, self._single_line(exc, 160))
                    return False
        logger.warning("当前配置对象没有可用保存方法,本次改动可能只在运行态生效")
        return False

    def _can_save_config(self) -> bool:
        config = getattr(self.plugin, "config", None)
        return any(callable(getattr(config, method_name, None)) for method_name in ("save_config", "save", "save_conf"))

    def _allowed_provider_keys(self) -> set[str]:
        keys = {
            "FAST_RESPONSE_PROVIDER_ID",
            "COMPLEX_REASONING_PROVIDER_ID",
            "CREATIVE_MODEL_PROVIDER_ID",
            "LLM_PROVIDER_ID",
            "MAI_STYLE_PROVIDER_ID",
            "DAILY_PLAN_PROVIDER_ID",
            "DETAIL_ENHANCEMENT_PROVIDER_ID",
            "DREAM_DIARY_PROVIDER_ID",
            "CREATIVE_PROVIDER_ID",
            "CREATIVE_OUTLINE_PROVIDER_ID",
            "CREATIVE_REVIEW_PROVIDER_ID",
            "VOICE_PROMPT_PROVIDER_ID",
            "tts_conversion_provider_id",
            "PHOTO_PROMPT_PROVIDER_ID",
            "NARRATION_PROVIDER_ID",
            "HISTORY_SUMMARY_PROVIDER_ID",
            "RESPONSE_REVIEW_PROVIDER_ID",
            "SMART_SILENCE_PROVIDER_ID",
            "PROACTIVE_PERSONA_JUDGE_PROVIDER_ID",
            "TROUBLESHOOTING_PROVIDER_ID",
            "DAILY_REVIEW_PROVIDER_ID",
            "SMART_MESSAGE_DEBOUNCE_PROVIDER_ID",
            "REST_WAKEUP_PROVIDER_ID",
            "RELATIONSHIP_ANALYSIS_PROVIDER_ID",
            "COMPANION_MEMORY_PROVIDER_ID",
            "DIALOGUE_EPISODE_PROVIDER_ID",
            "GROUP_INTERJECT_PROVIDER_ID",
            "GROUP_EPISODE_PROVIDER_ID",
            "GROUP_SLANG_PROVIDER_ID",
            "GROUP_FOLLOWUP_JUDGE_PROVIDER_ID",
            "FORWARD_MESSAGE_PROVIDER_ID",
            "PLUGIN_VISION_PROVIDER_ID",
            "READING_ARCHIVE_VISION_PROVIDER_ID",
            "NEWS_PROVIDER_ID",
            "WEB_EXPLORATION_PROVIDER_ID",
            "EMOTION_JUDGEMENT_PROVIDER_ID",
        }
        keys.update(self._schema_provider_keys(public_only=True))
        return keys

    def _normalize_schema_setting_value(self, value: Any, schema_item: dict[str, Any]) -> Any:
        item_type = str(schema_item.get("type") or "").lower()
        default = schema_item.get("default")
        slider = schema_item.get("slider") if isinstance(schema_item.get("slider"), dict) else {}

        def clamp_number(raw: float) -> float:
            if "min" in slider:
                try:
                    raw = max(float(slider.get("min")), raw)
                except (TypeError, ValueError):
                    pass
            if "max" in slider:
                try:
                    raw = min(float(slider.get("max")), raw)
                except (TypeError, ValueError):
                    pass
            return raw

        if item_type == "bool":
            return self._normalize_bool_value(value)
        if item_type == "int":
            try:
                return int(round(clamp_number(float(value))))
            except (TypeError, ValueError):
                try:
                    return int(default)
                except (TypeError, ValueError):
                    return 0
        if item_type == "float":
            try:
                return clamp_number(float(value))
            except (TypeError, ValueError):
                try:
                    return float(default)
                except (TypeError, ValueError):
                    return 0.0
        if item_type == "list":
            if isinstance(value, list):
                return [self._single_line(item, 160) for item in value if self._single_line(item, 160)]
            text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
            parts = re.split(r"[\n,，、\s]+", text)
            return [self._single_line(item, 160) for item in parts if self._single_line(item, 160)]
        limit = 4000 if item_type == "text" else 1000
        return str(value if value is not None else default or "").strip()[:limit]

    @staticmethod
    def _normalize_fractional_percent_value(value: Any, default: float = 0.0) -> float:
        try:
            raw = float(value)
        except (TypeError, ValueError):
            raw = default
        if raw > 1.0:
            raw /= 100.0
        return max(0.0, min(1.0, raw))

    @staticmethod
    def _normalize_multiline_source_config(value: Any, *, limit: int = 4000) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if text and "\n" not in text:
            markers = list(re.finditer(r"(?:^|\s+)(#?\s*[^|\n]+?)\|(?=(?:https?://|bilibili:|bvid:))", text, flags=re.I))
            if len(markers) > 1:
                recovered: list[str] = []
                for index, match in enumerate(markers):
                    start = match.end()
                    end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
                    name = str(match.group(1) or "").strip()
                    target = text[start:end].strip()
                    if name and target:
                        recovered.append(f"{name}|{target}")
                if recovered:
                    text = "\n".join(recovered)
        lines: list[str] = []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if line:
                lines.append(line)
        return "\n".join(lines)[:limit].strip()

    def _schema_key_index(self) -> dict[str, Any]:
        cached = self._schema_key_index_cache
        if cached is not None:
            return cached
        index: dict[str, Any] = {
            "all": set(),
            "public": set(),
            "bool": set(),
            "public_bool": set(),
            "provider": set(),
            "public_provider": set(),
            "group": {},
            "item": {},
        }

        def visit(items: dict[str, Any], group_key: str = "") -> None:
            for raw_key, item in items.items():
                if not isinstance(item, dict):
                    continue
                key = str(raw_key)
                item_type = str(item.get("type") or "")
                if item_type == "object" and isinstance(item.get("items"), dict):
                    visit(item["items"], key)
                    continue
                hidden = bool(item.get("invisible"))
                existing_item = index["item"].get(key)
                existing_group = str(index["group"].get(key) or "")
                existing_hidden = bool(existing_item.get("invisible")) if isinstance(existing_item, dict) else True
                prefer_candidate = (
                    existing_item is None
                    or (existing_hidden and not hidden)
                    or (existing_hidden == hidden and bool(group_key) and not existing_group)
                )
                if prefer_candidate:
                    index["group"][key] = group_key
                    index["item"][key] = item
                index["all"].add(key)
                if not hidden:
                    index["public"].add(key)
                if item_type == "bool":
                    index["bool"].add(key)
                    if not hidden:
                        index["public_bool"].add(key)
                if self._schema_item_is_provider(key, item):
                    index["provider"].add(key)
                    if not hidden:
                        index["public_provider"].add(key)

        try:
            raw = json.loads(Path(__file__).with_name("_conf_schema.json").read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                visit(raw)
        except Exception as exc:
            logger.debug("读取配置 schema 索引失败: %s", exc)
        self._schema_key_index_cache = index
        return index

    @staticmethod
    def _schema_item_is_provider(key: str, item: dict[str, Any]) -> bool:
        if item.get("_special") == "select_provider":
            return True
        return key.endswith("PROVIDER_ID") or key.endswith("_provider_id") or key.endswith("provider_id")

    def _schema_setting_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        key_name = "public" if public_only else "all"
        provider_key_name = "public_provider" if public_only else "provider"
        return set(index[key_name]) - set(index[provider_key_name])

    def _schema_provider_keys(self, *, public_only: bool = False) -> set[str]:
        index = self._schema_key_index()
        return set(index["public_provider" if public_only else "provider"])

    def _schema_bool_keys(self) -> set[str]:
        return set(self._schema_key_index()["bool"])

    def _schema_group_for_key(self, key: str) -> str:
        group_map = self._schema_key_index().get("group")
        return str(group_map.get(key, "") if isinstance(group_map, dict) else "")

    def _schema_item_for_key(self, key: str) -> dict[str, Any]:
        item_map = self._schema_key_index().get("item")
        item = item_map.get(key) if isinstance(item_map, dict) else None
        return item if isinstance(item, dict) else {}
