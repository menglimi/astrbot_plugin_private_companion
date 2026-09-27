# -*- coding: utf-8 -*-
"""PrivateCompanionPageApiMediaDiagnosticsPart04Mixin。

由 tools/split_mixin_domain.py 从 page_api_media_diagnostics.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 342 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPageApiMediaDiagnosticsMixin）。
"""
from __future__ import annotations

from .page_api_media_diagnostics_shared import logger
from .page_api_media_diagnostics_shared import Any
from .page_api_media_diagnostics_shared import CATALOG_VERSION
from .page_api_media_diagnostics_shared import CatalogValidationError
from .page_api_media_diagnostics_shared import _path_text
from .page_api_media_diagnostics_shared import load_catalog



class PrivateCompanionPageApiMediaDiagnosticsPart04Mixin:
    """PrivateCompanionPageApiMediaDiagnosticsPart04Mixin（从 PrivateCompanionPageApiMediaDiagnosticsMixin 拆出）。"""


    def _recent_photo_generation_summary(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        raw = data.get("recent_photo_generations")
        if not isinstance(raw, list):
            return []

        def compact_audit(values: Any) -> list[dict[str, str]]:
            if not isinstance(values, list):
                return []
            result: list[dict[str, str]] = []
            for value in values[:24]:
                if not isinstance(value, dict):
                    continue
                record = {
                    key: self._single_line(value.get(key), 120 if key == "preview" else 80)
                    for key in ("source", "section", "rule", "category", "action", "preview", "sha256")
                    if self._single_line(value.get(key), 120 if key == "preview" else 80)
                }
                if record:
                    result.append(record)
            return result

        items: list[dict[str, Any]] = []
        for item in raw[:8]:
            if not isinstance(item, dict):
                continue
            ts = self._float(item.get("ts"))
            prompt = str(item.get("prompt") or "")
            debug_payload = self._photo_prompt_debug_payload(item.get("prompt_path"))
            full_prompt = str(debug_payload.get("final_prompt") or "")
            raw_workflow_fixed = item.get("workflow_fixed_prompt")
            if not isinstance(raw_workflow_fixed, dict):
                raw_workflow_fixed = debug_payload.get("workflow_fixed_prompt")
            if not isinstance(raw_workflow_fixed, dict):
                raw_workflow_fixed = {}
            items.append(
                {
                    "ts": ts,
                    "time": self.plugin._format_timestamp_elapsed(ts),
                    "trace": self._single_line(item.get("trace"), 40),
                    "session": self._single_line(item.get("session"), 100),
                    "kind": self._single_line(item.get("kind"), 30),
                    "backend": self._single_line(item.get("backend"), 80),
                    "ok": bool(item.get("ok")),
                    "prompt_format": self._single_line(item.get("prompt_format"), 30),
                    "prompt": prompt[:500],
                    "full_prompt": full_prompt[:12000],
                    "prompt_preview": self._single_line(prompt, 180),
                    "prompt_hash": self._single_line(
                        item.get("prompt_hash") or debug_payload.get("final_prompt_sha256"),
                        80,
                    ),
                    "prompt_path": _path_text(item.get("prompt_path"), 1000),
                    "path": _path_text(item.get("path"), 1000),
                    "note": self._single_line(item.get("note"), 220),
                    "reference": bool(item.get("reference")),
                    "reference_used": bool(item.get("reference_used")),
                    "reference_path": _path_text(item.get("reference_path"), 1000),
                    "reference_id": self._single_line(item.get("reference_id"), 60),
                    "reference_kind": self._single_line(item.get("reference_kind"), 40),
                    "reference_roles": [
                        self._single_line(role, 40)
                        for role in (item.get("reference_roles") if isinstance(item.get("reference_roles"), list) else [])
                        if self._single_line(role, 40)
                    ][:8],
                    "reference_outfit_category": self._single_line(item.get("reference_outfit_category"), 40),
                    "image_size": self._single_line(item.get("image_size"), 40),
                    "elapsed_ms": self._int(item.get("elapsed_ms")),
                    "trigger": self._single_line(item.get("trigger"), 40),
                    "intent_kind": self._single_line(item.get("intent_kind"), 30),
                    "sent": bool(item.get("sent")),
                    "caption": self._single_line(item.get("caption"), 120),
                    "scene_preset": self._single_line(item.get("scene_preset"), 80),
                    "preset_hint": self._single_line(item.get("preset_hint"), 80),
                    "preset_source": self._single_line(item.get("preset_source"), 40),
                    "suggestion_status": self._single_line(item.get("suggestion_status"), 60),
                    "wardrobe_mode": self._single_line(item.get("wardrobe_mode"), 40),
                    "wardrobe_source": self._single_line(item.get("wardrobe_source"), 40),
                    "wardrobe_category": self._single_line(item.get("wardrobe_category"), 40),
                    "outfit_locked": bool(item.get("outfit_locked")),
                    "daily_outfit_removed": bool(item.get("daily_outfit_removed")),
                    "wardrobe_reason": self._single_line(item.get("wardrobe_reason"), 240),
                    "conflicts": [
                        self._single_line(value, 120)
                        for value in (item.get("conflicts") if isinstance(item.get("conflicts"), list) else [])
                        if self._single_line(value, 120)
                    ][:12],
                    "removed_conflicts": [
                        self._single_line(value, 120)
                        for value in (item.get("removed_conflicts") if isinstance(item.get("removed_conflicts"), list) else [])
                        if self._single_line(value, 120)
                    ][:12],
                    "residual_conflicts": [
                        self._single_line(value, 120)
                        for value in (
                            item.get("residual_conflicts")
                            if isinstance(item.get("residual_conflicts"), list)
                            else []
                        )
                        if self._single_line(value, 120)
                    ][:12],
                    "reference_removed": bool(item.get("reference_removed")),
                    "reference_removal": (
                        dict(item.get("reference_removal"))
                        if isinstance(item.get("reference_removal"), dict)
                        else {}
                    ),
                    "sanitizer_version": self._int(item.get("sanitizer_version")),
                    "workflow_fixed_prompt": {
                        "scope": self._single_line(raw_workflow_fixed.get("scope"), 30),
                        "config_key": self._single_line(
                            raw_workflow_fixed.get("config_key"), 80
                        ),
                        "configured": bool(raw_workflow_fixed.get("configured")),
                        "normalized": bool(raw_workflow_fixed.get("normalized")),
                        "normalization_changed": bool(
                            raw_workflow_fixed.get("normalization_changed")
                        ),
                        "conflict_cleaned": bool(
                            raw_workflow_fixed.get("conflict_cleaned")
                        ),
                        "cleaned": bool(raw_workflow_fixed.get("cleaned")),
                        "applied": bool(raw_workflow_fixed.get("applied")),
                        "raw_length": self._int(raw_workflow_fixed.get("raw_length")),
                        "normalized_length": self._int(
                            raw_workflow_fixed.get("normalized_length")
                        ),
                        "applied_length": self._int(
                            raw_workflow_fixed.get("applied_length")
                        ),
                        "removed_rules": [
                            self._single_line(value, 80)
                            for value in (
                                raw_workflow_fixed.get("removed_rules")
                                if isinstance(raw_workflow_fixed.get("removed_rules"), list)
                                else []
                            )
                            if self._single_line(value, 80)
                        ][:12],
                    },
                    "detected_conflicts": compact_audit(item.get("detected_conflicts")),
                    "removed_conflict_details": compact_audit(item.get("removed_conflict_details")),
                    "residual_conflict_details": compact_audit(item.get("residual_conflict_details")),
                    "tool_name": self._single_line(item.get("tool_name"), 60),
                    "presets": [
                        self._single_line(name, 40)
                        for name in (item.get("presets") if isinstance(item.get("presets"), list) else [])
                        if self._single_line(name, 40)
                    ][:1],
                }
            )
        return items

    def _sync_legacy_external_image_api_config_from_endpoints(self, endpoints: list[dict[str, Any]]) -> None:
        normalized = endpoints if isinstance(endpoints, list) else []
        first = normalized[0] if len(normalized) >= 1 and isinstance(normalized[0], dict) else {}
        second = normalized[1] if len(normalized) >= 2 and isinstance(normalized[1], dict) else {}

        def endpoint_complete(endpoint: dict[str, Any]) -> bool:
            return bool(
                endpoint
                and endpoint.get("enabled", True)
                and str(endpoint.get("base_url") or "").strip()
                and str(endpoint.get("api_key") or "").strip()
                and str(endpoint.get("model") or "").strip()
            )

        updates = {
            "external_image_api_platform": first.get("platform", "auto") if first else "auto",
            "EXTERNAL_IMAGE_API_BASE_URL": first.get("base_url", "") if first else "",
            "EXTERNAL_IMAGE_API_KEY": first.get("api_key", "") if first else "",
            "EXTERNAL_IMAGE_API_MODEL": first.get("model", "") if first else "",
            "external_image_api_size": first.get("size", "1024x1024") if first else "1024x1024",
            "external_image_api_timeout_seconds": self._int(first.get("timeout_seconds"), 180, 20, 600) if first else 180,
            "external_image_api_custom_headers": first.get("custom_headers", "") if first else "",
            "enable_backup_external_image_api": endpoint_complete(second),
            "backup_external_image_api_platform": second.get("platform", "auto") if second else "auto",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": second.get("base_url", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": second.get("api_key", "") if second else "",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": second.get("model", "") if second else "",
            "backup_external_image_api_size": second.get("size", "1024x1024") if second else "1024x1024",
            "backup_external_image_api_timeout_seconds": self._int(second.get("timeout_seconds"), 180, 20, 600) if second else 180,
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
            self._set_config_value(key, value)
            setattr(self.plugin, attr_map[key], value)

    def _sync_photo_generation_runtime_config(self) -> None:
        reference_enabled = self._config_get("enable_photo_reference_image")
        if reference_enabled not in ("", None):
            self.plugin.enable_photo_reference_image = self._normalize_bool_value(reference_enabled)
        structured_enabled = self._config_get("enable_p5_structured_reference_assets")
        if structured_enabled not in ("", None):
            self.plugin.enable_p5_structured_reference_assets = self._normalize_bool_value(structured_enabled)
        relationship_enabled = self._config_get("enable_bot_relationship_network")
        if relationship_enabled not in ("", None):
            self.plugin.enable_bot_relationship_network = self._normalize_bool_value(relationship_enabled)
        enabled_backup = self._config_get("enable_backup_external_image_api")
        if enabled_backup not in ("", None):
            self.plugin.enable_backup_external_image_api = self._normalize_bool_value(enabled_backup)
        use_environment_proxy = self._config_get("external_image_download_use_environment_proxy")
        if use_environment_proxy not in ("", None):
            self.plugin.external_image_download_use_environment_proxy = self._normalize_bool_value(use_environment_proxy)
        mapping = {
            "photo_generation_backend": "photo_generation_backend",
            "custom_photo_tool_name": "custom_photo_tool_name",
            "custom_photo_tool_prompt_param": "custom_photo_tool_prompt_param",
            "custom_photo_tool_kind_param": "custom_photo_tool_kind_param",
            "custom_photo_tool_reference_param": "custom_photo_tool_reference_param",
            "custom_photo_tool_extra_params": "custom_photo_tool_extra_params",
            "COMFYUI_TEXT2IMG_WORKFLOW_NAME": "comfyui_text2img_workflow_name",
            "COMFYUI_SELFIE_WORKFLOW_NAME": "comfyui_selfie_workflow_name",
            "photo_reference_catalog": "photo_reference_catalog",
            "photo_persona_reference_image_path": "photo_persona_reference_image_path",
            "photo_reference_library": "photo_reference_library",
            "enable_wardrobe": "enable_wardrobe",
            "wardrobe_tendency": "wardrobe_tendency",
            "enable_wardrobe_prompt": "enable_wardrobe_prompt",
            "wardrobe_prompt_max_items": "wardrobe_prompt_max_items",
            "wardrobe_image_max_count": "wardrobe_image_max_count",
            "wardrobe_image_prompt": "wardrobe_image_prompt",
            "WARDROBE_VISION_PROVIDER_ID": "wardrobe_vision_provider_id",
            "wardrobe_items": "wardrobe_items",
            "wardrobe_photo_source": "wardrobe_photo_source",
            "daily_outfit_photo_prompt": "daily_outfit_photo_prompt",
            "daily_outfit_rotation_days": "daily_outfit_rotation_days",
            "external_image_api_platform": "external_image_api_platform",
            "EXTERNAL_IMAGE_API_BASE_URL": "external_image_api_base_url",
            "EXTERNAL_IMAGE_API_KEY": "external_image_api_key",
            "EXTERNAL_IMAGE_API_MODEL": "external_image_api_model",
            "external_image_api_size": "external_image_api_size",
            "external_image_api_custom_headers": "external_image_api_custom_headers",
            "external_image_download_proxy": "external_image_download_proxy",
            "backup_external_image_api_platform": "backup_external_image_api_platform",
            "BACKUP_EXTERNAL_IMAGE_API_BASE_URL": "backup_external_image_api_base_url",
            "BACKUP_EXTERNAL_IMAGE_API_KEY": "backup_external_image_api_key",
            "BACKUP_EXTERNAL_IMAGE_API_MODEL": "backup_external_image_api_model",
            "backup_external_image_api_size": "backup_external_image_api_size",
            "backup_external_image_api_custom_headers": "backup_external_image_api_custom_headers",
            "external_image_api_endpoints": "external_image_api_endpoints",
            "photo_generation_prompt_format": "photo_generation_prompt_format",
            "photo_generation_style": "photo_generation_style",
            "photo_generation_style_custom_prompt": "photo_generation_style_custom_prompt",
            "photo_generation_negative_prompt_mode": "photo_generation_negative_prompt_mode",
            "photo_generation_negative_prompt": "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt": "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt": "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt": "photo_generation_edit_negative_prompt",
            "photo_generation_fixed_prompt": "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt": "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt": "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt": "photo_generation_edit_fixed_prompt",
            "photo_generation_scene_presets": "photo_generation_scene_presets",
            "bot_relationship_cards": "bot_relationship_cards",
        }
        for key, attr in mapping.items():
            value = self._config_get_raw(key) if key in {"external_image_api_endpoints", "photo_reference_catalog", "photo_reference_library", "bot_relationship_cards"} else self._config_get(key)
            if key == "external_image_api_endpoints":
                normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
                endpoints = normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
                self.plugin.external_image_api_endpoints = endpoints
                continue
            if key == "photo_reference_catalog":
                try:
                    serialized_catalog = self._normalize_setting_value(key, value)
                    loaded_catalog = load_catalog(
                        serialized_catalog,
                        catalog_version=CATALOG_VERSION,
                        preset_names=self._photo_reference_preset_names(),
                    )
                    self.plugin.photo_reference_catalog = loaded_catalog.references
                    self.plugin.photo_reference_catalog_version = CATALOG_VERSION
                    self.plugin.photo_reference_catalog_read_only = loaded_catalog.read_only
                except CatalogValidationError as exc:
                    logger.warning("忽略无效的运行时参考图目录同步: %s", self._single_line(exc, 180))
                continue
            if key == "photo_reference_library":
                normalized_library = self._normalize_setting_value(key, value)
                setattr(self.plugin, attr, normalized_library if isinstance(normalized_library, list) else [])
                continue
            if key == "bot_relationship_cards":
                normalized_cards = self._normalize_setting_value(key, value)
                setattr(self.plugin, attr, normalized_cards if isinstance(normalized_cards, list) else [])
                continue
            if key == "photo_persona_reference_image_path":
                setattr(self.plugin, attr, str(value or "").strip())
                continue
            if value not in ("", None):
                text = str(value).strip()
                if key == "photo_generation_backend":
                    text = text.lower()
                    if text not in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai", "anima_master"}:
                        text = "auto"
                elif key == "photo_generation_prompt_format":
                    normalizer = getattr(self.plugin, "_normalize_photo_generation_prompt_format", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"traditional", "natural_language", "nai"}:
                        text = "traditional"
                elif key == "photo_generation_negative_prompt_mode":
                    normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"safe_default", "merge", "replace"}:
                        text = "safe_default"
                elif key in {"external_image_api_platform", "backup_external_image_api_platform"}:
                    normalizer = getattr(self.plugin, "_normalize_external_image_api_platform", None)
                    text = normalizer(text) if callable(normalizer) else text.lower()
                    if text not in {"auto", "openai", "openrouter", "agnes", "sensenova", "bailian", "modelscope", "doubao", "gemini", "minimax"}:
                        text = "auto"
                setattr(self.plugin, attr, text)
        timeout = self._config_get("external_image_api_timeout_seconds")
        if timeout not in ("", None):
            try:
                self.plugin.external_image_api_timeout_seconds = max(20, min(600, int(float(timeout))))
            except Exception:
                pass
        backup_timeout = self._config_get("backup_external_image_api_timeout_seconds")
        if backup_timeout not in ("", None):
            try:
                self.plugin.backup_external_image_api_timeout_seconds = max(20, min(600, int(float(backup_timeout))))
            except Exception:
                pass
        wait_seconds = self._config_get("comfyui_photo_wait_seconds")
        if wait_seconds not in ("", None):
            try:
                self.plugin.comfyui_photo_wait_seconds = max(5, min(600, int(float(wait_seconds))))
            except Exception:
                pass
