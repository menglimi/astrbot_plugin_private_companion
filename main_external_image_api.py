# -*- coding: utf-8 -*-
"""外部图像 API 域。

由 tools/split_main_domain_v2.py 从 main.py 机械抽取（3 个方法 / 217 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 PrivateCompanionPlugin）。
"""
from __future__ import annotations

import json
import re
from .helpers import _safe_int, _single_line
from typing import Any
from urllib.parse import urlparse

class PrivateCompanionPluginExternalImageApiMixin:
    """外部图像 API 域（从 PrivateCompanionPlugin 拆出）。"""

    @staticmethod
    def _normalize_external_image_api_platform(value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "auto": "auto",
            "自动": "auto",
            "openai": "openai",
            "openai-compatible": "openai",
            "openai_compatible": "openai",
            "openai兼容": "openai",
            "兼容": "openai",
            "兼容模式": "openai",
            "external": "openai",
            "openrouter": "openrouter",
            "open-router": "openrouter",
            "open_router": "openrouter",
            "openrouter.ai": "openrouter",
            "agnes": "agnes",
            "agnes-ai": "agnes",
            "agnes_ai": "agnes",
            "agnes image": "agnes",
            "agnes-image": "agnes",
            "sapiens": "agnes",
            "sapiens ai": "agnes",
            "bailian": "bailian",
            "dashscope": "bailian",
            "aliyun": "bailian",
            "alibaba": "bailian",
            "modelstudio": "bailian",
            "model_studio": "bailian",
            "百炼": "bailian",
            "阿里云百炼": "bailian",
            "通义万相": "bailian",
            "modelscope": "modelscope",
            "model_scope": "modelscope",
            "魔搭": "modelscope",
            "魔搭社区": "modelscope",
            "api-inference": "modelscope",
            "doubao": "doubao",
            "豆包": "doubao",
            "火山": "doubao",
            "火山引擎": "doubao",
            "volcengine": "doubao",
            "volces": "doubao",
            "ark": "doubao",
            "seedream": "doubao",
            "seed": "doubao",
            "gemini": "gemini",
            "google": "gemini",
            "google-ai": "gemini",
            "google_ai": "gemini",
            "generativelanguage": "gemini",
            "nano-banana": "gemini",
            "sensenova": "sensenova",
            "sense-nova": "sensenova",
            "日日新": "sensenova",
            "商汤日日新": "sensenova",
            "minimax": "minimax",
            "minimaxi": "minimax",
            "minimax-ai": "minimax",
            "minimax_ai": "minimax",
            "海螺": "minimax",
            "海螺ai": "minimax",
        }
        return aliases.get(text, text if text in {"auto", "openai", "openrouter", "agnes", "bailian", "modelscope", "doubao", "gemini", "sensenova", "minimax"} else "auto")

    def _normalize_external_image_api_endpoint(self, item: Any, *, index: int = 0) -> dict[str, Any]:
        if not isinstance(item, dict):
            return {}

        def pick(*keys: str, default: Any = "") -> Any:
            for key in keys:
                if key in item and item.get(key) not in (None, ""):
                    return item.get(key)
            return default

        endpoint = {
            "name": _single_line(pick("name", "label", "title", default=f"在线 API {index + 1}"), 80) or f"在线 API {index + 1}",
            "enabled": self._normalize_external_image_endpoint_enabled(pick("enabled", "enable", "active", default=True), True),
            "platform": self._normalize_external_image_api_platform(
                pick("platform", "external_image_api_platform", "image_api_platform", default="auto")
            ),
            "base_url": str(
                pick(
                    "base_url",
                    "api_base",
                    "api_base_url",
                    "url",
                    "endpoint",
                    "EXTERNAL_IMAGE_API_BASE_URL",
                    "BACKUP_EXTERNAL_IMAGE_API_BASE_URL",
                    default="",
                )
                or ""
            ).strip(),
            "api_key": str(
                pick(
                    "api_key",
                    "key",
                    "token",
                    "EXTERNAL_IMAGE_API_KEY",
                    "BACKUP_EXTERNAL_IMAGE_API_KEY",
                    default="",
                )
                or ""
            ).strip(),
            "model": str(
                pick(
                    "model",
                    "model_name",
                    "EXTERNAL_IMAGE_API_MODEL",
                    "BACKUP_EXTERNAL_IMAGE_API_MODEL",
                    default="",
                )
                or ""
            ).strip(),
            "size": str(
                pick("size", "image_size", "external_image_api_size", "backup_external_image_api_size", default="1024x1024")
                or "1024x1024"
            ).strip()
            or "1024x1024",
            "ratio": _single_line(pick("ratio", "aspect_ratio", "image_ratio", default=""), 20),
            "timeout_seconds": _safe_int(
                pick(
                    "timeout_seconds",
                    "timeout",
                    "external_image_api_timeout_seconds",
                    "backup_external_image_api_timeout_seconds",
                    default=180,
                ),
                180,
                20,
                600,
            ),
            "custom_headers": str(
                pick(
                    "custom_headers",
                    "headers",
                    "external_image_api_custom_headers",
                    "backup_external_image_api_custom_headers",
                    default="",
                )
                or ""
            ).strip(),
        }
        base_lower = str(endpoint.get("base_url") or "").lower()
        model_lower = str(endpoint.get("model") or "").lower()
        parsed_base = urlparse(base_lower if "://" in base_lower else f"https://{base_lower}")
        base_host = str(parsed_base.hostname or "").strip().lower()
        if endpoint["platform"] in {"auto", "openai", "openrouter"} and (
            base_host == "openrouter.ai" or base_host.endswith(".openrouter.ai")
        ):
            endpoint["platform"] = "openrouter"
        if endpoint["platform"] in {"auto", "openai"} and (
            "apihub.agnes-ai.com" in base_lower or model_lower.startswith("agnes-image-")
        ):
            endpoint["platform"] = "agnes"
        minimax_official_host = any(
            host in base_lower
            for host in ("api.minimaxi.com", "api.minimax.io", "minimaxi.com", "minimax.io")
        )
        if (
            endpoint["platform"] in {"auto", "openai"} and minimax_official_host
        ) or (
            endpoint["platform"] == "auto" and model_lower in {"image-01", "image-01-live"}
        ):
            endpoint["platform"] = "minimax"
        if endpoint["platform"] == "minimax" and re.search(
            r"/v1/(?:image_generation|image/generation|images/generations|images/edits)/?(?:[?#].*)?$",
            str(endpoint.get("base_url") or ""),
            flags=re.I,
        ):
            base_normalizer = getattr(self, "_normalized_external_image_api_base_url", None)
            if callable(base_normalizer):
                normalized_root = base_normalizer(endpoint["base_url"], platform="minimax")
                if normalized_root:
                    endpoint["base_url"] = f"{normalized_root.rstrip('/')}/image_generation"
        if endpoint["platform"] == "auto" and ("token.sensenova.cn" in base_lower or model_lower in {"senova-u1-fast", "sensenova-u1-fast"}):
            endpoint["platform"] = "sensenova"
        if endpoint["platform"] == "sensenova" and model_lower == "senova-u1-fast":
            endpoint["model"] = "sensenova-u1-fast"
        return endpoint

    def _normalize_external_image_api_endpoints(self, value: Any) -> list[dict[str, Any]]:
        raw = value
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                raw = []
            else:
                try:
                    parsed = json.loads(text)
                    raw = parsed
                except Exception:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    raw = [{"base_url": line} for line in lines]
        if isinstance(raw, dict):
            raw = raw.get("items") or raw.get("endpoints") or raw.get("apis") or []
        if not isinstance(raw, list):
            return []
        normalized: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for index, item in enumerate(raw[:12]):
            endpoint = self._normalize_external_image_api_endpoint(item, index=index)
            if not endpoint:
                continue
            if not any(str(endpoint.get(key) or "").strip() for key in ("base_url", "api_key", "model", "custom_headers")):
                continue
            signature = (
                str(endpoint.get("platform") or "auto").lower(),
                str(endpoint.get("base_url") or "").rstrip("/"),
                str(endpoint.get("model") or ""),
                str(endpoint.get("api_key") or "")[:12],
            )
            if signature in seen:
                continue
            seen.add(signature)
            normalized.append(endpoint)
        return normalized
