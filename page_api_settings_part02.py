# -*- coding: utf-8 -*-
"""PageSettingNormalizerPart02Mixin。

由 tools/split_mixin_domain.py 从 page_api_settings.py 机械抽取（4 个方法 + 0 个模块级名字 + 0 个类级赋值 / 387 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PageSettingNormalizerMixin）。
"""
from __future__ import annotations

import json
import re
from .helpers import _path_text, normalize_bot_relationship_cards, normalize_photo_generation_scopes
from .page_api_settings_shared import _SETTING_UNHANDLED
from .photo_generation_scope import PHOTO_GENERATION_SCOPE_LIMIT_KEYS, normalize_photo_generation_scope_limit
from .photo_reference_catalog import CatalogValidationError, validate_and_serialize
from .wardrobe import (
    WARDROBE_MAX_ITEMS,
    WARDROBE_PROMPT_MAX_ITEMS,
    normalize_wardrobe_image_prompt,
    normalize_wardrobe_items,
    normalize_wardrobe_outfits,
    normalize_wardrobe_tendency,
)
from copy import deepcopy
from typing import Any



class PageSettingNormalizerPart02Mixin:
    """PageSettingNormalizerPart02Mixin（从 PageSettingNormalizerMixin 拆出）。"""


    def _normalize_page_voice_photo_setting(self, key: str, value: Any) -> Any:
        if key == "rest_reply_mode":
            mode = str(value or "probability").strip().lower()
            aliases = {
                "概率": "probability",
                "仅概率": "probability",
                "仅概率醒来": "probability",
                "模型": "llm",
                "模型判断": "llm",
                "模型判断是否醒来": "llm",
                "model": "llm",
                "llm_judge": "llm",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"probability", "llm"} else "probability"
        if key == "REST_WAKEUP_PROVIDER_ID":
            return str(value or "").strip()[:160]
        if key == "tts_synthesis_backend":
            mode = str(value or "astrbot_provider").strip().lower()
            aliases = {
                "astrbot": "astrbot_provider",
                "provider": "astrbot_provider",
                "official": "astrbot_provider",
                "官方": "astrbot_provider",
                "mimo": "mimo_voice_clone",
                "mimotts": "mimo_voice_clone",
                "mimo_plugin": "mimo_voice_clone",
                "插件": "mimo_voice_clone",
                "自动": "auto",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"astrbot_provider", "mimo_voice_clone", "auto"} else "astrbot_provider"
        if key == "tts_generation_mode":
            mode = str(value or "fast_tag").strip().lower()
            aliases = {
                "hybrid": "fast_tag",
                "direct": "fast_tag",
                "tag": "fast_tag",
                "fast": "fast_tag",
                "快速": "fast_tag",
                "标签": "fast_tag",
                "标签直出": "fast_tag",
                "convert": "postprocess",
                "post": "postprocess",
                "llm": "postprocess",
                "后处理": "postprocess",
                "判断翻译": "postprocess",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"fast_tag", "postprocess"} else "fast_tag"
        if key == "tts_frequency_control_mode":
            mode = str(value or "global").strip().lower()
            aliases = {
                "全局": "global",
                "全局频控": "global",
                "新版": "global",
                "旧版": "legacy",
                "旧版行为": "legacy",
                "legacy_mode": "legacy",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"global", "legacy"} else "global"
        if key == "tts_constraint_mode":
            mode = str(value or "weak").strip().lower()
            aliases = {
                "弱": "weak",
                "弱约束": "weak",
                "软": "weak",
                "软约束": "weak",
                "强": "strong",
                "强约束": "strong",
                "硬": "strong",
                "硬约束": "strong",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"weak", "strong"} else "weak"
        if key == "tts_voice_language":
            lang = str(value or "zh").strip().lower()
            return lang if lang in {"ja", "zh", "en"} else "zh"
        if key in {"tts_provider_id_zh", "tts_provider_id_ja", "tts_provider_id_en"}:
            return str(value or "").strip()[:160]
        if key == "tts_fishaudio_model":
            model = str(value or "auto").strip().lower()
            return model if model in {"auto", "s2.1-pro-free", "s2.1-pro", "s2-pro", "s1"} else "auto"
        if key == "tts_fishaudio_emotion_mode":
            mode = str(value or "balanced").strip().lower()
            return mode if mode in {"balanced", "expressive", "manual"} else "balanced"
        if key == "tts_delivery_mode":
            mode = str(value or "voice_and_text").strip().lower()
            return mode if mode in {"voice_only", "voice_and_text"} else "voice_and_text"
        if key == "tts_foreign_text_mode":
            mode = str(value or "translation").strip().lower()
            return mode if mode in {"original", "translation", "bilingual"} else "translation"
        if key == "tts_conversion_scope":
            mode = str(value or "partial").strip().lower()
            return mode if mode in {"partial", "full"} else "partial"
        if key in {"tts_extra_prompt", "main_user_mention_voice_prompt"}:
            return str(value or "").strip()[:1200]
        if key == "tts_trigger_keywords":
            return str(value or "").strip()[:2000]
        if key in {
            "natural_language_photo_extra_prompt",
            "photo_generation_fixed_prompt",
            "photo_generation_text2img_fixed_prompt",
            "photo_generation_selfie_fixed_prompt",
            "photo_generation_edit_fixed_prompt",
            "photo_generation_negative_prompt",
            "photo_generation_text2img_negative_prompt",
            "photo_generation_selfie_negative_prompt",
            "photo_generation_edit_negative_prompt",
            "photo_generation_scene_presets",
        }:
            return str(value or "").strip()[:5000]
        if key == "photo_generation_prompt_format":
            normalizer = getattr(self.plugin, "_normalize_photo_generation_prompt_format", None)
            if callable(normalizer):
                return normalizer(value)
            mode = str(value or "traditional").strip().lower().replace("-", "_")
            if mode in {"nai", "novelai", "nai4", "nai_4", "nai45", "nai_diffusion", "naidiffusion"}:
                return "nai"
            return "natural_language" if mode in {"natural", "natural_language", "description", "prose", "自然语言", "自然语言描述"} else "traditional"
        if key == "photo_generation_negative_prompt_mode":
            normalizer = getattr(self.plugin, "_normalize_photo_generation_negative_prompt_mode", None)
            if callable(normalizer):
                return normalizer(value)
            mode = str(value or "safe_default").strip().lower().replace("-", "_")
            aliases = {
                "合并": "merge",
                "合并自定义": "merge",
                "替换": "replace",
                "完全替换": "replace",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"safe_default", "merge", "replace"} else "safe_default"
        if key == "natural_language_photo_generation_mode":
            mode = str(value or "tool_first").strip().lower()
            aliases = {
                "tool": "tool_first",
                "工具": "tool_first",
                "工具优先": "tool_first",
                "规则": "rule_fast",
                "规则快判": "rule_fast",
                "快判": "rule_fast",
                "关闭": "off",
                "关": "off",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"tool_first", "rule_fast", "off"} else "tool_first"
        if key == "tts_conversion_provider_id":
            return str(value or "").strip()[:160]
        if key == "tts_session_min_interval_seconds":
            try:
                return max(0.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return 90.0
        if key in {"tts_private_min_interval_seconds", "tts_group_min_interval_seconds"}:
            try:
                return max(-1.0, min(3600.0, float(value)))
            except (TypeError, ValueError):
                return -1.0
        if key == "main_user_mention_voice_keywords":
            return str(value or "").strip()[:1200]
        if key == "forward_message_mode":
            mode = str(value or "inject").strip().lower()
            if mode in {"注入", "injection"}:
                return "inject"
            if mode in {"转述", "summary", "summarize", "narrate", "relay"}:
                return "transcribe"
            return mode if mode in {"inject", "transcribe"} else "inject"
        if key == "photo_generation_backend":
            mode = str(value or "auto").strip().lower()
            return mode if mode in {"auto", "comfyui", "sdgen", "external", "tool_call", "nai"} else "auto"
        if key == "photo_generation_allowed_scopes":
            return normalize_photo_generation_scopes(value)
        if key in {"photo_structured_reference_assets", "owned_reaction_assets"}:
            raw_items = value
            if isinstance(raw_items, str):
                try:
                    raw_items = json.loads(raw_items or "[]")
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_items = []
            if not isinstance(raw_items, list):
                return []
            limit = 16 if key == "photo_structured_reference_assets" else 96
            return [deepcopy(item) for item in raw_items if isinstance(item, dict)][:limit]
        if key in PHOTO_GENERATION_SCOPE_LIMIT_KEYS.values():
            return normalize_photo_generation_scope_limit(value)
        if key == "photo_reference_catalog":
            raw_items = value
            if isinstance(value, str):
                try:
                    raw_items = json.loads(value or "[]")
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CatalogValidationError({"photo_reference_catalog": ["目录必须是 JSON 数组"]}) from exc
            if not isinstance(raw_items, list):
                raise CatalogValidationError({"photo_reference_catalog": ["目录必须是数组"]})
            return validate_and_serialize(raw_items, preset_names=self._photo_reference_preset_names())
        if key == "bot_relationship_cards":
            normalizer = getattr(self.plugin, "_normalize_bot_relationship_cards", None)
            if callable(normalizer):
                return normalizer(value)
            return normalize_bot_relationship_cards(value)
        if key == "photo_reference_library":
            return self._normalize_photo_reference_library(value)
        if key == "wardrobe_items":
            return self._normalize_wardrobe_items(value)
        if key == "wardrobe_outfits":
            return normalize_wardrobe_outfits(value)
        if key == "wardrobe_photo_source":
            # 默认不接管：只有明确写了 wardrobe（或「衣柜」）才交给衣柜，
            # 其余（含空值、拼错、旧的 built-in 写法）一律沿用作者候选表。
            raw_source = str(value or "").strip().casefold()
            return "wardrobe" if raw_source in {"wardrobe", "衣柜", "跟随衣柜"} else "builtin"
        if key == "wardrobe_outfit_mode":
            # 兜底与 schema / bootstrap 的默认值对齐（都应该是 select）。
            return "inventory" if str(value or "").strip().casefold() == "inventory" else "select"
        if key == "wardrobe_injection_detail":
            return (
                "progressive"
                if str(value or "").strip().casefold() == "progressive"
                else "full"
            )
        if key == "wardrobe_outfit_rotation_days":
            return self._normalize_wardrobe_int(value, 7, 1, 30)
        if key == "wardrobe_tendency":
            return normalize_wardrobe_tendency(value)
        if key == "wardrobe_image_prompt":
            return normalize_wardrobe_image_prompt(value)
        if key == "wardrobe_prompt_max_items":
            return self._normalize_wardrobe_int(value, WARDROBE_PROMPT_MAX_ITEMS, 1, WARDROBE_MAX_ITEMS)
        if key == "wardrobe_image_max_count":
            return self._normalize_wardrobe_int(value, 3, 1, 8)
        if key in {"enable_wardrobe", "enable_wardrobe_prompt", "enable_wardrobe_outfit_generate"}:
            return self._normalize_bool_value(value)
        if key in {"WARDROBE_VISION_PROVIDER_ID", "WARDROBE_OUTFIT_PROVIDER_ID"}:
            return str(value or "").strip()[:160]
        if key == "external_image_api_endpoints":
            normalizer = getattr(self.plugin, "_normalize_external_image_api_endpoints", None)
            return normalizer(value) if callable(normalizer) else (value if isinstance(value, list) else [])
        if key in {"external_image_api_platform", "backup_external_image_api_platform"}:
            mode = str(value or "auto").strip().lower()
            aliases = {
                "openai兼容": "openai",
                "openai-compatible": "openai",
                "openrouter": "openrouter",
                "open-router": "openrouter",
                "open_router": "openrouter",
                "openrouter.ai": "openrouter",
                "agnes": "agnes",
                "agnes-ai": "agnes",
                "agnes_ai": "agnes",
                "sapiens": "agnes",
                "百炼": "bailian",
                "阿里云百炼": "bailian",
                "dashscope": "bailian",
                "modelscope": "modelscope",
                "model_scope": "modelscope",
                "魔搭": "modelscope",
                "魔搭社区": "modelscope",
                "api-inference": "modelscope",
                "doubao": "doubao",
                "豆包": "doubao",
                "火山": "doubao",
                "火山引擎": "doubao",
                "seedream": "doubao",
                "volcengine": "doubao",
                "gemini": "gemini",
                "google": "gemini",
                "谷歌": "gemini",
                "generativelanguage": "gemini",
                "sensenova": "sensenova",
                "sense-nova": "sensenova",
                "日日新": "sensenova",
                "minimax": "minimax",
                "minimaxi": "minimax",
                "minimax-ai": "minimax",
                "minimax_ai": "minimax",
                "海螺": "minimax",
                "海螺ai": "minimax",
            }
            mode = aliases.get(mode, mode)
            return mode if mode in {"auto", "openai", "openrouter", "agnes", "sensenova", "bailian", "modelscope", "doubao", "gemini", "minimax"} else "auto"
        return _SETTING_UNHANDLED

    def _normalize_wardrobe_int(self, value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))

    def _normalize_wardrobe_items(self, value: Any) -> list[dict[str, Any]]:
        """Accept a JSON string or a real list and return normalized wardrobe rows."""

        raw_items = value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            try:
                raw_items = json.loads(text)
            except (TypeError, ValueError, json.JSONDecodeError):
                return []
        if not isinstance(raw_items, list):
            return []
        return normalize_wardrobe_items(raw_items)

    def _normalize_photo_reference_library(self, value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            raw_items = value
        else:
            raw_text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
            raw_items = []
            parsed_array = False
            if raw_text.startswith("[") and raw_text.endswith("]"):
                try:
                    parsed_items = json.loads(raw_text)
                    if isinstance(parsed_items, list):
                        raw_items = parsed_items
                        parsed_array = True
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            if not parsed_array and raw_text:
                raw_items = raw_text.split("\n")
        items: list[dict[str, Any]] = []
        seen_sources: set[str] = set()
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                item = dict(raw_item)
            else:
                text = str(raw_item or "").strip()
                if not text:
                    continue
                item = {}
                if text.startswith("{") and text.endswith("}"):
                    try:
                        parsed_item = json.loads(text)
                        if isinstance(parsed_item, dict):
                            item = dict(parsed_item)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        pass
                if not item:
                    parts = re.split(r"\s*(?:\|\||｜｜)\s*", text, maxsplit=2)
                    item = {
                        "path": parts[0] if parts else "",
                        "note": parts[1] if len(parts) > 1 else "",
                    }
                    if len(parts) > 2:
                        metadata_text = str(parts[2] or "").strip()
                        if metadata_text.startswith("{"):
                            try:
                                metadata = json.loads(metadata_text)
                                if isinstance(metadata, dict):
                                    item.update(
                                        {
                                            name: field_value
                                            for name, field_value in metadata.items()
                                            if name not in {"source", "path", "url", "note", "description"}
                                        }
                                    )
                                else:
                                    item["note"] = f"{item['note']} || {metadata_text}".strip(" |")
                            except (TypeError, ValueError, json.JSONDecodeError):
                                item["note"] = f"{item['note']} || {metadata_text}".strip(" |")
                        else:
                            item["note"] = f"{item['note']} || {metadata_text}".strip(" |")

            source = _path_text(item.get("source") or item.get("path") or item.get("url"), 1000)
            if not source or source in seen_sources:
                continue
            seen_sources.add(source)
            note = str(item.get("note") or item.get("description") or "")
            note = note.replace("\r\n", "\n").replace("\r", "\n").strip()[:500]
            item["path"] = source
            item["note"] = note
            for field in ("reference_roles", "scene_categories", "time_categories"):
                if field in item and not isinstance(item.get(field), list):
                    item[field] = [
                        part
                        for part in re.split(r"[,，、/|\s]+", str(item.get(field) or ""))
                        if part
                    ]
            if "outfit_lock_default" in item:
                raw_lock = item.get("outfit_lock_default")
                if raw_lock is None or (isinstance(raw_lock, str) and not raw_lock.strip()):
                    item.pop("outfit_lock_default", None)
                else:
                    item["outfit_lock_default"] = self._normalize_bool_value(raw_lock)
            items.append(item)
        return items[:24]
