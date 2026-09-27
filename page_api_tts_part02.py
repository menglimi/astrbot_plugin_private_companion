# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiTtsPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_tts.py 机械抽取（12 个方法 + 0 个模块级名字 + 0 个类级赋值 / 490 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiTtsMixin）。
"""
from __future__ import annotations

from .page_api_tts_shared import FISH_AUDIO_MODEL_OPTIONS, TTS_PROVIDER_SYSTEM_KEYS, logger
from .page_api_tts_shared import Any
from .page_api_tts_shared import _redact_outbound_secrets
from .page_api_tts_shared import asyncio
from .page_api_tts_shared import deepcopy
from .page_api_tts_shared import json
from .page_api_tts_shared import re
from .page_api_tts_shared import request
from .page_api_tts_shared import secrets
from .page_api_tts_shared import time



class PrivateCompanionPageApiTtsPart02Mixin:
    """PrivateCompanionPageApiTtsPart02Mixin（从 PrivateCompanionPageApiTtsMixin 拆出）。"""


    def _tts_provider_schema_bundle(self) -> dict[str, Any]:
        config_templates: dict[str, Any] = {}
        field_metadata: dict[str, Any] = {}
        try:
            from astrbot.core.config.default import CONFIG_METADATA_2

            provider_metadata = (
                CONFIG_METADATA_2.get("provider_group", {})
                .get("metadata", {})
                .get("provider", {})
            )
            config_templates = deepcopy(provider_metadata.get("config_template", {}) or {})
            field_metadata = deepcopy(provider_metadata.get("items", {}) or {})
        except Exception as exc:
            logger.warning(
                "读取 AstrBot TTS Provider 模板失败，将使用运行态字段: %s",
                self._single_line(exc, 160),
            )

        templates: list[dict[str, Any]] = []
        by_type: dict[str, dict[str, Any]] = {}
        for display_name, source in config_templates.items():
            if not self._is_tts_provider_config(source):
                continue
            defaults = deepcopy(source)
            provider_type = self._single_line(defaults.get("type"), 80)
            if not provider_type:
                continue
            if provider_type == "fishaudio_tts_api":
                defaults.setdefault("model", "s2.1-pro-free")

            fields: list[dict[str, Any]] = []
            for key, default in defaults.items():
                if key in TTS_PROVIDER_SYSTEM_KEYS:
                    continue
                metadata = deepcopy(field_metadata.get(key, {}) or {})
                if provider_type == "fishaudio_tts_api" and key == "model":
                    metadata.update(
                        {
                            "type": "string",
                            "description": "Fish Audio 模型",
                            "hint": "S2/S2.1 使用方括号自然语言情绪控制；S1 使用旧版圆括号控制。",
                            "options": deepcopy(FISH_AUDIO_MODEL_OPTIONS),
                        }
                    )
                options = metadata.get("options") if isinstance(metadata.get("options"), list) else []
                option_labels = metadata.get("labels") if isinstance(metadata.get("labels"), list) else []
                normalized_options: list[dict[str, str]] = []
                for index, option in enumerate(options):
                    if isinstance(option, dict):
                        value = str(option.get("value", "") or "")
                        label = str(option.get("label", value) or value)
                    else:
                        value = str(option or "")
                        label = str(option_labels[index] or value) if index < len(option_labels) else value
                    normalized_options.append({"value": value, "label": label})
                slider = metadata.get("slider") if isinstance(metadata.get("slider"), dict) else {}
                fallback_label = self._tts_provider_fallback_label(key)
                raw_label = self._single_line(metadata.get("description"), 120)
                if (
                    not raw_label
                    or self._tts_provider_metadata_unresolved(raw_label)
                    or (re.search(r"[\u4e00-\u9fff]", fallback_label) and not re.search(r"[\u4e00-\u9fff]", raw_label))
                ):
                    raw_label = fallback_label
                raw_hint = self._multi_line(metadata.get("hint"), 600)
                if self._tts_provider_metadata_unresolved(raw_hint):
                    raw_hint = self._tts_provider_fallback_hint(key)
                fields.append(
                    {
                        "key": key,
                        "label": raw_label,
                        "type": self._tts_provider_field_type(default, metadata),
                        "hint": raw_hint,
                        "options": normalized_options,
                        "default": deepcopy(default),
                        "secret": self._tts_provider_secret_field(key),
                        "group": self._tts_provider_field_group(key),
                        "min": metadata.get("min", slider.get("min")),
                        "max": metadata.get("max", slider.get("max")),
                        "step": metadata.get("step", slider.get("step")),
                    }
                )

            template = {
                "name": self._single_line(display_name, 120),
                "type": provider_type,
                "provider": self._single_line(defaults.get("provider"), 80),
                "hint": self._multi_line(defaults.get("hint"), 600),
                "default_id": self._single_line(defaults.get("id"), 80),
                "defaults": {
                    key: deepcopy(value)
                    for key, value in defaults.items()
                    if key not in {"hint"}
                },
                "fields": fields,
            }
            templates.append(template)
            by_type[provider_type] = template

        templates.sort(key=lambda item: (item["name"].lower(), item["type"]))
        return {"templates": templates, "by_type": by_type}

    def _tts_provider_runtime_configs(self) -> list[dict[str, Any]]:
        manager = self._tts_provider_manager()
        configs = list(getattr(manager, "providers_config", None) or [])
        return [deepcopy(item) for item in configs if self._is_tts_provider_config(item)]

    def _serialize_tts_provider_config(
        self,
        config: dict[str, Any],
        schema_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        manager = self._tts_provider_manager()
        provider_id = self._single_line(config.get("id"), 160)
        provider_type = self._single_line(config.get("type"), 80)
        template = schema_bundle.get("by_type", {}).get(provider_type, {})
        merged = deepcopy(config)
        merger = getattr(manager, "get_merged_provider_config", None)
        if callable(merger):
            try:
                merged = merger(config)
            except Exception:
                merged = deepcopy(config)

        fields = deepcopy(template.get("fields", []) or [])
        known_keys = {str(field.get("key") or "") for field in fields}
        for key, value in merged.items():
            if key in TTS_PROVIDER_SYSTEM_KEYS or key in known_keys:
                continue
            fields.append(
                {
                    "key": key,
                    "label": self._tts_provider_fallback_label(key),
                    "type": self._tts_provider_field_type(value, {}),
                    "hint": "",
                    "options": [],
                    "default": "",
                    "secret": self._tts_provider_secret_field(key),
                    "group": self._tts_provider_field_group(key),
                }
            )

        values: dict[str, Any] = {}
        secret_configured: dict[str, bool] = {}
        for field in fields:
            key = str(field.get("key") or "")
            value = deepcopy(merged.get(key, field.get("default", "")))
            if field.get("secret"):
                secret_configured[key] = value not in (None, "", [], {})
                value = ""
            values[key] = value

        loaded = provider_id in (getattr(manager, "inst_map", {}) or {})
        using_id = ""
        context = getattr(self.plugin, "context", None)
        getter = getattr(context, "get_using_tts_provider", None)
        if callable(getter):
            try:
                using_id = self._provider_id(getter())
            except Exception:
                using_id = ""
        return {
            "id": provider_id,
            "name": self._single_line(template.get("name"), 120) or provider_id,
            "type": provider_type,
            "provider": self._single_line(config.get("provider"), 80),
            "provider_source_id": self._single_line(config.get("provider_source_id"), 160),
            "enable": bool(config.get("enable", False)),
            "loaded": loaded,
            "is_default": provider_id == using_id,
            "model": self._single_line(merged.get("model") or merged.get("gemini_tts_model"), 160),
            "values": values,
            "secret_configured": secret_configured,
            "fields": fields,
        }

    def _tts_provider_management_payload(self) -> dict[str, Any]:
        schema_bundle = self._tts_provider_schema_bundle()
        items = [
            self._serialize_tts_provider_config(config, schema_bundle)
            for config in self._tts_provider_runtime_configs()
        ]
        items.sort(key=lambda item: (not item["enable"], not item["loaded"], item["name"].lower(), item["id"].lower()))
        return {
            "items": items,
            "templates": schema_bundle.get("templates", []),
            "fish_audio_models": deepcopy(FISH_AUDIO_MODEL_OPTIONS),
            "total": len(items),
            "enabled": sum(1 for item in items if item.get("enable")),
            "loaded": sum(1 for item in items if item.get("loaded")),
        }

    @staticmethod
    def _coerce_tts_provider_field(value: Any, field: dict[str, Any]) -> Any:
        field_type = str(field.get("type") or "string").lower()
        if field_type == "bool":
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        if field_type == "int":
            try:
                return int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是整数") from exc
        if field_type == "float":
            try:
                return float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是数字") from exc
        if field_type in {"object", "list"}:
            parsed = value
            if isinstance(value, str):
                text = value.strip()
                if not text:
                    return {} if field_type == "object" else []
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{field.get('label') or field.get('key')} 不是有效 JSON") from exc
            if field_type == "object" and not isinstance(parsed, dict):
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是 JSON 对象")
            if field_type == "list" and not isinstance(parsed, list):
                raise ValueError(f"{field.get('label') or field.get('key')} 必须是 JSON 数组")
            return parsed
        return str(value or "").strip()[:12000]

    def _normalized_tts_provider_update(
        self,
        current: dict[str, Any],
        incoming: dict[str, Any],
        schema_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        provider_type = self._single_line(current.get("type"), 80)
        template = schema_bundle.get("by_type", {}).get(provider_type, {})
        fields = list(template.get("fields", []) or [])
        field_map = {str(field.get("key") or ""): field for field in fields}
        for key, value in current.items():
            if key in TTS_PROVIDER_SYSTEM_KEYS or key in field_map:
                continue
            field_map[key] = {
                "key": key,
                "label": self._tts_provider_fallback_label(key),
                "type": self._tts_provider_field_type(value, {}),
                "secret": self._tts_provider_secret_field(key),
            }

        normalized = deepcopy(current)
        if "enable" in incoming:
            normalized["enable"] = self._coerce_tts_provider_field(
                incoming.get("enable"), {"key": "enable", "label": "启用", "type": "bool"}
            )
        values = incoming.get("values") if isinstance(incoming.get("values"), dict) else {}
        for key, value in values.items():
            field = field_map.get(str(key))
            if not field:
                continue
            if field.get("secret") and value in (None, ""):
                continue
            normalized[str(key)] = self._coerce_tts_provider_field(value, field)

        if provider_type == "fishaudio_tts_api" and "model" in values:
            model = str(normalized.get("model") or "").strip().lower()
            allowed = {str(item["value"]) for item in FISH_AUDIO_MODEL_OPTIONS}
            if model not in allowed:
                raise ValueError("Fish Audio 模型不在支持列表中")
            normalized["model"] = model
        normalized["id"] = self._single_line(current.get("id"), 160)
        normalized["type"] = provider_type
        normalized["provider_type"] = "text_to_speech"
        return normalized

    async def list_tts_provider_configs(self) -> dict[str, Any]:
        try:
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            logger.error("获取 TTS Provider 配置失败: %s", exc, exc_info=True)
            return self._error(self._single_line(exc, 240))

    async def create_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_type = self._single_line(payload.get("type"), 80)
        provider_id = self._single_line(payload.get("id"), 80)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,79}", provider_id):
            return self._error("Provider ID 只能包含字母、数字、点、下划线、冒号和短横线")
        try:
            manager = self._tts_provider_manager()
            schema_bundle = self._tts_provider_schema_bundle()
            template = schema_bundle.get("by_type", {}).get(provider_type)
            if not isinstance(template, dict):
                return self._error("不支持该 TTS Provider 类型")
            config = deepcopy(template.get("defaults", {}) or {})
            config.update(
                {
                    "id": provider_id,
                    "type": provider_type,
                    "provider": self._single_line(template.get("provider"), 80),
                    "provider_type": "text_to_speech",
                    "enable": False,
                }
            )
            if provider_type == "fishaudio_tts_api":
                config["model"] = "s2.1-pro-free"
            creator = getattr(manager, "create_provider", None)
            if not callable(creator):
                return self._error("当前 AstrBot 不支持动态创建 Provider")
            await creator(config)
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    @staticmethod
    def _tts_language_clone_provider_id(
        source_provider_id: str,
        language: str,
        existing_ids: set[str],
    ) -> str:
        source = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(source_provider_id or "")).strip("._:-") or "tts"
        language = language if language in {"zh", "ja", "en"} else "voice"
        existing_lower = {str(item or "").lower() for item in existing_ids}
        for index in range(1, 1000):
            suffix = f"-{language}" if index == 1 else f"-{language}-{index}"
            base = source[: max(1, 80 - len(suffix))].rstrip("._:-") or "tts"
            candidate = f"{base}{suffix}"
            if candidate.lower() not in existing_lower:
                return candidate
        raise ValueError("无法生成不重复的语种专用 Provider ID")

    async def clone_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        source_provider_id = self._single_line(payload.get("source_provider_id"), 160)
        language = self._single_line(payload.get("language"), 8).lower()
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        if not source_provider_id:
            return self._error("缺少源 TTS Provider ID")
        if language not in {"zh", "ja", "en"}:
            return self._error("语种必须是 zh、ja 或 en")
        try:
            manager = self._tts_provider_manager()
            getter = getattr(manager, "get_provider_config_by_id", None)
            current = getter(source_provider_id) if callable(getter) else next(
                (deepcopy(item) for item in self._tts_provider_runtime_configs() if item.get("id") == source_provider_id),
                None,
            )
            if not self._is_tts_provider_config(current):
                return self._error("源 TTS Provider 不存在")
            runtime_configs = self._tts_provider_runtime_configs()
            clone_id = self._tts_language_clone_provider_id(
                source_provider_id,
                language,
                {self._single_line(item.get("id"), 160) for item in runtime_configs},
            )
            normalized = self._normalized_tts_provider_update(
                current,
                incoming,
                self._tts_provider_schema_bundle(),
            )
            normalized["id"] = clone_id
            creator = getattr(manager, "create_provider", None)
            if not callable(creator):
                return self._error("当前 AstrBot 不支持动态创建 Provider")
            await creator(normalized)
            result = self._tts_provider_management_payload()
            result.update(
                {
                    "provider_id": clone_id,
                    "source_provider_id": source_provider_id,
                    "language": language,
                }
            )
            logger.info(
                "已复制语种专用 TTS Provider: language=%s source=%s clone=%s",
                language,
                source_provider_id,
                clone_id,
            )
            return self._ok(result)
        except Exception as exc:
            logger.warning(
                "复制语种专用 TTS Provider 失败: language=%s source=%s error=%s",
                language,
                source_provider_id,
                self._single_line(_redact_outbound_secrets(str(exc)), 240),
            )
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    async def update_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_id = self._single_line(payload.get("provider_id"), 160)
        incoming = payload.get("config") if isinstance(payload.get("config"), dict) else {}
        if not provider_id:
            return self._error("缺少 TTS Provider ID")
        try:
            manager = self._tts_provider_manager()
            getter = getattr(manager, "get_provider_config_by_id", None)
            current = getter(provider_id) if callable(getter) else next(
                (deepcopy(item) for item in self._tts_provider_runtime_configs() if item.get("id") == provider_id),
                None,
            )
            if not self._is_tts_provider_config(current):
                return self._error("TTS Provider 不存在")
            schema_bundle = self._tts_provider_schema_bundle()
            normalized = self._normalized_tts_provider_update(current, incoming, schema_bundle)
            if normalized == current:
                return self._ok(self._tts_provider_management_payload())
            updater = getattr(manager, "update_provider", None)
            if not callable(updater):
                return self._error("当前 AstrBot 不支持动态更新 Provider")
            await updater(provider_id, normalized)
            return self._ok(self._tts_provider_management_payload())
        except Exception as exc:
            logger.warning(
                "TTS Provider 保存失败: provider=%s error=%s",
                provider_id,
                self._single_line(_redact_outbound_secrets(str(exc)), 240),
            )
            return self._error(self._single_line(_redact_outbound_secrets(str(exc)), 240))

    async def test_tts_provider_config(self) -> dict[str, Any]:
        payload = await request.get_json(silent=True) or {}
        provider_id = self._single_line(payload.get("provider_id"), 160)
        request_id = secrets.token_hex(6)
        start = time.time()
        logger.info("[test:%s][type:tts_provider_connection] 开始执行测试", request_id)
        try:
            manager = self._tts_provider_manager()
            config_getter = getattr(manager, "get_provider_config_by_id", None)
            config = config_getter(provider_id) if callable(config_getter) else None
            if not self._is_tts_provider_config(config):
                result = {
                    "ok": False,
                    "provider_id": provider_id,
                    "error": "TTS Provider 不存在",
                    "steps": [{"name": "配置检查", "status": "error", "detail": "没有找到对应的 TTS Provider 配置"}],
                }
            else:
                provider = (getattr(manager, "inst_map", {}) or {}).get(provider_id)
                if provider is None:
                    result = {
                        "ok": False,
                        "provider_id": provider_id,
                        "error": "Provider 尚未启用或加载失败，请先保存并启用",
                        "steps": [
                            {"name": "配置检查", "status": "ok", "detail": "已找到 TTS Provider 配置"},
                            {"name": "实例加载", "status": "error", "detail": "Provider 尚未启用或实例加载失败"},
                        ],
                    }
                else:
                    tester = getattr(provider, "test", None)
                    if not callable(tester):
                        result = {
                            "ok": False,
                            "provider_id": provider_id,
                            "error": "该 Provider 不支持测试",
                            "steps": [
                                {"name": "配置检查", "status": "ok", "detail": "已找到并加载 TTS Provider"},
                                {"name": "自检能力", "status": "error", "detail": "Provider 没有提供 test() 自检入口"},
                            ],
                        }
                    else:
                        call_started = time.time()
                        await asyncio.wait_for(tester(), timeout=90.0)
                        result = {
                            "ok": True,
                            "provider_id": provider_id,
                            "detail": "TTS Provider 自检调用完成",
                            "steps": [
                                {"name": "配置检查", "status": "ok", "detail": "已找到并加载 TTS Provider"},
                                {
                                    "name": "Provider 自检",
                                    "status": "ok",
                                    "detail": "test() 调用成功完成",
                                    "elapsed_ms": int((time.time() - call_started) * 1000),
                                },
                            ],
                        }
        except Exception as exc:
            safe_error = self._safe_test_diagnostic_text(exc, 1600)
            if isinstance(exc, asyncio.TimeoutError) and not safe_error:
                safe_error = "TTS Provider 自检超过 90 秒仍未完成"
            result = {
                "ok": False,
                "provider_id": provider_id,
                "error": safe_error or "TTS Provider 自检失败",
                "exception_type": exc.__class__.__name__,
            }
        result["elapsed_ms"] = int((time.time() - start) * 1000)
        result["request_id"] = request_id
        result = self._finalize_test_diagnostics(
            "tts_provider_connection",
            result,
            start,
            title="TTS Provider 连接测试",
        )
        logger.info(
            "[test:%s][type:tts_provider_connection] 测试结束: status=%s elapsed_ms=%s",
            request_id,
            result.get("test_status"),
            result.get("elapsed_ms"),
        )
        return self._ok(result)
