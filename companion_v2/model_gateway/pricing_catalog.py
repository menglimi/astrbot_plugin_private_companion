"""Pure stdlib price catalog, multi-source adapters, candidate scorer, and atomic store.

Zero network I/O and zero host imports live here. HTTP fetching lives strictly
in ``astrbot_host/pricing_sources.py``, which passes decoded payloads into the
pure ``adapt_*`` functions and persists snapshots through ``PriceCatalogStore``.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any

from .pricing_expr import (
    PricingExprError,
    lower_newapi_billing_expr,
    validate_tiered_expr_rule,
)


MAX_CATALOG_BYTES = 32 * 1024 * 1024  # 32 MB on-disk safety cap
AUTO_MATCH_THRESHOLD = 0.85
PER_CALL_AUTO_MATCH_THRESHOLD = 0.96
MAX_CANDIDATES_PER_MODEL = 12
MAX_PRICE_PER_MILLION = 1_000_000.0
DEFAULT_NEWAPI_QUOTA_PER_UNIT = 500_000.0
NEWAPI_SITE_META_SCHEMA_VERSION = 2
_RMB_RECHARGE_PRICE_UPPER_BOUND = 4.5
_RMB_PAY_METHOD_TYPES = frozenset({"alipay", "wxpay", "wechat", "wechatpay", "epay_alipay", "epay_wxpay"})
_NON_CUSTOM_CURRENCY_SYMBOLS = frozenset({"$", "US$", "USD", "¤", "€", "£"})

PUBLIC_SOURCE_SPECS: tuple[dict[str, str], ...] = (
    {
        "source_id": "modelsdev",
        "label": "models.dev",
        "url": "https://models.dev/catalog.json",
        "kind": "public",
    },
    {
        "source_id": "litellm",
        "label": "LiteLLM",
        "url": "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
        "kind": "public",
    },
    {
        "source_id": "openrouter",
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/models",
        "kind": "public",
    },
)
PUBLIC_SOURCE_IDS: frozenset[str] = frozenset(spec["source_id"] for spec in PUBLIC_SOURCE_SPECS)
_SOURCE_PRIORITY = {"modelsdev": 1, "litellm": 2, "openrouter": 3}

_CATALOG_LOCK = threading.RLock()


@dataclass(frozen=True)
class PresetTariff:
    preset_id: str
    vendor: str
    label: str
    input: int
    cached_input: int
    cache_creation: int | None
    output: int

    def to_tariff(self):
        from .pricing import UsageTariff
        return UsageTariff(
            self.input,
            self.cached_input,
            self.output,
            cache_creation=self.cache_creation,
            configured=True,
        )

    def to_rule(self) -> dict[str, Any]:
        inp = round(self.input / 1_000_000, 6)
        cached = round(self.cached_input / 1_000_000, 6)
        creation = round(self.cache_creation / 1_000_000, 6) if self.cache_creation is not None else None
        out = round(self.output / 1_000_000, 6)
        return {
            "mode": "per_token",
            "currency": "USD",
            "input": inp,
            "input_cached": cached,
            "cache_creation": creation,
            "output": out,
            "multiplier": 1.0,
            "free": (inp == 0.0 and cached == 0.0 and out == 0.0 and (creation is None or creation == 0.0)),
            "configured": {
                "input": True,
                "input_cached": True,
                "cache_creation": creation is not None,
                "output": True,
            },
        }

    def to_usd_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "vendor": self.vendor,
            "label": self.label,
            "currency": "USD",
            "input_per_million": round(self.input / 1_000_000, 6),
            "cached_input_per_million": round(self.cached_input / 1_000_000, 6),
            "cache_creation_per_million": (
                round(self.cache_creation / 1_000_000, 6) if self.cache_creation is not None else None
            ),
            "output_per_million": round(self.output / 1_000_000, 6),
        }


def _usd(value: float) -> int:
    return int(round(value * 1_000_000))


def _preset(
    preset_id: str,
    vendor: str,
    label: str,
    inp: float,
    cached: float,
    out: float,
    *,
    cache_creation: float | None = None,
) -> PresetTariff:
    return PresetTariff(
        preset_id=preset_id,
        vendor=vendor,
        label=label,
        input=_usd(inp),
        cached_input=_usd(cached),
        cache_creation=_usd(cache_creation) if cache_creation is not None else None,
        output=_usd(out),
    )


# Reference public list prices in USD per 1M tokens (final fallback when no
# user override or synced online catalog price matches).
_BUILTIN_PRESETS: tuple[PresetTariff, ...] = (
    # OpenAI GPT-5 / GPT-4.1 / GPT-4o / GPT-4 / o-series
    _preset("gpt-5.4-pro", "OpenAI", "GPT-5.4 Pro", 15.0, 1.5, 120.0),
    _preset("gpt-5.4-mini", "OpenAI", "GPT-5.4 Mini", 0.75, 0.075, 4.5),
    _preset("gpt-5.4-nano", "OpenAI", "GPT-5.4 Nano", 0.2, 0.02, 1.25),
    _preset("gpt-5.4", "OpenAI", "GPT-5.4", 2.5, 0.25, 15.0),
    _preset("gpt-5.3", "OpenAI", "GPT-5.3", 1.75, 0.175, 14.0),
    _preset("gpt-5.2-pro", "OpenAI", "GPT-5.2 Pro", 21.0, 2.1, 168.0),
    _preset("gpt-5.2", "OpenAI", "GPT-5.2", 1.75, 0.175, 14.0),
    _preset("gpt-5.1", "OpenAI", "GPT-5.1", 1.25, 0.125, 10.0),
    _preset("gpt-5-pro", "OpenAI", "GPT-5 Pro", 15.0, 1.5, 120.0),
    _preset("gpt-5-mini", "OpenAI", "GPT-5 Mini", 0.25, 0.025, 2.0),
    _preset("gpt-5-nano", "OpenAI", "GPT-5 Nano", 0.05, 0.005, 0.4),
    _preset("gpt-5", "OpenAI", "GPT-5", 1.25, 0.125, 10.0),
    _preset("gpt-4.1-mini", "OpenAI", "GPT-4.1 Mini", 0.4, 0.1, 1.6),
    _preset("gpt-4.1-nano", "OpenAI", "GPT-4.1 Nano", 0.1, 0.025, 0.4),
    _preset("gpt-4.1", "OpenAI", "GPT-4.1", 2.0, 0.5, 8.0),
    _preset("gpt-4o-mini", "OpenAI", "GPT-4o Mini", 0.15, 0.075, 0.6),
    _preset("gpt-4o", "OpenAI", "GPT-4o", 2.5, 1.25, 10.0),
    _preset("gpt-4-turbo", "OpenAI", "GPT-4 Turbo", 10.0, 5.0, 30.0),
    _preset("gpt-4", "OpenAI", "GPT-4", 30.0, 30.0, 60.0),
    _preset("o4-mini", "OpenAI", "o4-mini", 1.1, 0.275, 4.4),
    _preset("o3-pro", "OpenAI", "o3-pro", 20.0, 5.0, 80.0),
    _preset("o3-mini", "OpenAI", "o3-mini", 1.1, 0.55, 4.4),
    _preset("o3", "OpenAI", "o3", 2.0, 0.5, 8.0),
    _preset("o1-pro", "OpenAI", "o1-pro", 150.0, 75.0, 600.0),
    _preset("o1-mini", "OpenAI", "o1-mini", 1.1, 0.55, 4.4),
    _preset("o1", "OpenAI", "o1", 15.0, 7.5, 60.0),
    # Anthropic Claude (cache read 0.1x, 5m cache write 1.25x)
    _preset("claude-opus-4-6", "Anthropic", "Claude Opus 4.6", 5.0, 0.5, 25.0, cache_creation=6.25),
    _preset("claude-sonnet-4-6", "Anthropic", "Claude Sonnet 4.6", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-opus-4-5", "Anthropic", "Claude Opus 4.5", 5.0, 0.5, 25.0, cache_creation=6.25),
    _preset("claude-sonnet-4-5", "Anthropic", "Claude Sonnet 4.5", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-haiku-4-5", "Anthropic", "Claude Haiku 4.5", 1.0, 0.1, 5.0, cache_creation=1.25),
    _preset("claude-opus-4-1", "Anthropic", "Claude Opus 4.1", 15.0, 1.5, 75.0, cache_creation=18.75),
    _preset("claude-opus-4", "Anthropic", "Claude Opus 4", 15.0, 1.5, 75.0, cache_creation=18.75),
    _preset("claude-sonnet-4", "Anthropic", "Claude Sonnet 4", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-3-7-sonnet", "Anthropic", "Claude 3.7 Sonnet", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-3-5-sonnet", "Anthropic", "Claude 3.5 Sonnet", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-3-5-haiku", "Anthropic", "Claude 3.5 Haiku", 0.8, 0.08, 4.0, cache_creation=1.0),
    _preset("claude-3-opus", "Anthropic", "Claude 3 Opus", 15.0, 1.5, 75.0, cache_creation=18.75),
    _preset("claude-3-sonnet", "Anthropic", "Claude 3 Sonnet", 3.0, 0.3, 15.0, cache_creation=3.75),
    _preset("claude-3-haiku", "Anthropic", "Claude 3 Haiku", 0.25, 0.03, 1.25, cache_creation=0.3),
    # Google Gemini
    _preset("gemini-3.1-pro", "Google", "Gemini 3.1 Pro", 2.0, 0.2, 12.0),
    _preset("gemini-3.1-flash-lite", "Google", "Gemini 3.1 Flash-Lite", 0.25, 0.025, 1.5),
    _preset("gemini-3-pro", "Google", "Gemini 3 Pro", 2.0, 0.2, 12.0),
    _preset("gemini-3-flash", "Google", "Gemini 3 Flash", 0.5, 0.05, 3.0),
    _preset("gemini-2.5-pro", "Google", "Gemini 2.5 Pro", 1.25, 0.125, 10.0),
    _preset("gemini-2.5-flash-lite", "Google", "Gemini 2.5 Flash-Lite", 0.1, 0.01, 0.4),
    _preset("gemini-2.5-flash", "Google", "Gemini 2.5 Flash", 0.3, 0.03, 2.5),
    _preset("gemini-2.0-flash-lite", "Google", "Gemini 2.0 Flash-Lite", 0.075, 0.01875, 0.3),
    _preset("gemini-2.0-flash", "Google", "Gemini 2.0 Flash", 0.1, 0.025, 0.4),
    _preset("gemini-1.5-pro", "Google", "Gemini 1.5 Pro", 1.25, 0.3125, 5.0),
    _preset("gemini-1.5-flash", "Google", "Gemini 1.5 Flash", 0.075, 0.01875, 0.3),
    # DeepSeek
    _preset("deepseek-v4", "DeepSeek", "DeepSeek V4", 0.3, 0.03, 0.5),
    _preset("deepseek-reasoner", "DeepSeek", "DeepSeek Reasoner", 0.28, 0.028, 0.42),
    _preset("deepseek-r1", "DeepSeek", "DeepSeek R1", 0.55, 0.14, 2.19),
    _preset("deepseek-chat", "DeepSeek", "DeepSeek Chat", 0.28, 0.028, 0.42),
    _preset("deepseek-v3.2", "DeepSeek", "DeepSeek V3.2", 0.28, 0.028, 0.42),
    _preset("deepseek-v3.1", "DeepSeek", "DeepSeek V3.1", 0.28, 0.028, 0.42),
    _preset("deepseek-v3", "DeepSeek", "DeepSeek V3", 0.27, 0.07, 1.1),
    # Alibaba Qwen
    _preset("qwen3.5-plus", "Qwen", "Qwen3.5 Plus", 0.4, 0.08, 1.2),
    _preset("qwen3-max", "Qwen", "Qwen3 Max", 1.2, 0.24, 6.0),
    _preset("qwen3-plus", "Qwen", "Qwen3 Plus", 0.4, 0.08, 1.2),
    _preset("qwen3-flash", "Qwen", "Qwen3 Flash", 0.05, 0.01, 0.4),
    _preset("qwen3-coder-plus", "Qwen", "Qwen3 Coder Plus", 1.0, 0.2, 5.0),
    _preset("qwen3-vl-plus", "Qwen", "Qwen3 VL Plus", 0.4, 0.08, 1.2),
    _preset("qwen3-235b", "Qwen", "Qwen3 235B", 0.3, 0.06, 1.2),
    _preset("qwen3-32b", "Qwen", "Qwen3 32B", 0.1, 0.02, 0.3),
    _preset("qwen-max", "Qwen", "Qwen Max", 1.6, 0.32, 6.4),
    _preset("qwen-plus", "Qwen", "Qwen Plus", 0.4, 0.08, 1.2),
    _preset("qwen-flash", "Qwen", "Qwen Flash", 0.05, 0.01, 0.4),
    _preset("qwen-turbo", "Qwen", "Qwen Turbo", 0.05, 0.01, 0.2),
    _preset("qwen-vl-max", "Qwen", "Qwen VL Max", 0.8, 0.16, 3.2),
    _preset("qwen-vl-plus", "Qwen", "Qwen VL Plus", 0.21, 0.042, 0.63),
    _preset("qwen2.5-72b", "Qwen", "Qwen2.5 72B", 0.35, 0.07, 1.0),
    _preset("qwen2.5-32b", "Qwen", "Qwen2.5 32B", 0.15, 0.03, 0.45),
    _preset("qwq-32b", "Qwen", "QwQ 32B", 0.15, 0.03, 0.45),
    # Zhipu GLM
    _preset("glm-5", "Zhipu", "GLM-5", 0.8, 0.16, 2.8),
    _preset("glm-4.7", "Zhipu", "GLM-4.7", 0.6, 0.11, 2.2),
    _preset("glm-4.6", "Zhipu", "GLM-4.6", 0.6, 0.11, 2.2),
    _preset("glm-4.5-flash", "Zhipu", "GLM-4.5 Flash", 0.0, 0.0, 0.0),
    _preset("glm-4.5-air", "Zhipu", "GLM-4.5 Air", 0.2, 0.04, 1.1),
    _preset("glm-4.5", "Zhipu", "GLM-4.5", 0.6, 0.11, 2.2),
    _preset("glm-4-plus", "Zhipu", "GLM-4 Plus", 0.7, 0.14, 0.7),
    _preset("glm-4-air", "Zhipu", "GLM-4 Air", 0.14, 0.028, 0.14),
    _preset("glm-4-flash", "Zhipu", "GLM-4 Flash", 0.0, 0.0, 0.0),
    # ByteDance Doubao
    _preset("doubao-seed-1-6", "Doubao", "Doubao Seed 1.6", 0.11, 0.022, 0.28),
    _preset("doubao-1-5-pro", "Doubao", "Doubao 1.5 Pro", 0.11, 0.022, 0.28),
    _preset("doubao-1-5-lite", "Doubao", "Doubao 1.5 Lite", 0.042, 0.008, 0.084),
    _preset("doubao-pro", "Doubao", "Doubao Pro", 0.11, 0.022, 0.28),
    _preset("doubao-lite", "Doubao", "Doubao Lite", 0.042, 0.008, 0.084),
    # MiniMax
    _preset("minimax-m2.5", "MiniMax", "MiniMax M2.5", 0.3, 0.03, 1.2),
    _preset("minimax-m2", "MiniMax", "MiniMax M2", 0.3, 0.03, 1.2),
    _preset("minimax-m1", "MiniMax", "MiniMax M1", 0.4, 0.04, 2.2),
    _preset("minimax-text-01", "MiniMax", "MiniMax Text-01", 0.2, 0.02, 1.1),
    # Moonshot Kimi
    _preset("kimi-k2.5", "Moonshot", "Kimi K2.5", 0.6, 0.1, 2.5),
    _preset("kimi-k2", "Moonshot", "Kimi K2", 0.6, 0.15, 2.5),
    _preset("moonshot-v1-128k", "Moonshot", "Moonshot v1 128K", 1.1, 0.22, 1.1),
    _preset("moonshot-v1-32k", "Moonshot", "Moonshot v1 32K", 0.42, 0.084, 0.42),
    _preset("moonshot-v1-8k", "Moonshot", "Moonshot v1 8K", 0.17, 0.034, 0.17),
    # xAI Grok
    _preset("grok-4", "xAI", "Grok 4", 3.0, 0.75, 15.0),
    _preset("grok-3-mini", "xAI", "Grok 3 Mini", 0.3, 0.075, 0.5),
    _preset("grok-3", "xAI", "Grok 3", 3.0, 0.75, 15.0),
    # Meta Llama & Mistral
    _preset("llama-4-maverick", "Meta", "Llama 4 Maverick", 0.2, 0.05, 0.6),
    _preset("llama-4-scout", "Meta", "Llama 4 Scout", 0.1, 0.025, 0.3),
    _preset("llama-3.3-70b", "Meta", "Llama 3.3 70B", 0.23, 0.06, 0.4),
    _preset("mistral-large", "Mistral", "Mistral Large", 2.0, 0.5, 6.0),
    _preset("codestral", "Mistral", "Codestral", 0.3, 0.075, 0.9),
)

DEFAULT_MODEL_TARIFFS: dict[str, PresetTariff] = {item.preset_id: item for item in _BUILTIN_PRESETS}

_VENDOR_PREFIXES = (
    "openai/", "anthropic/", "google/", "deepseek/", "qwen/", "alibaba/", "dashscope/",
    "zhipu/", "z-ai/", "bigmodel/", "moonshot/", "moonshotai/", "minimax/", "bytedance/",
    "doubao/", "volcengine/", "x-ai/", "xai/", "openrouter/", "vertex_ai/", "vertex/",
    "bedrock/", "azure/", "models/", "publishers/google/models/", "publishers/anthropic/models/",
    "meta-llama/", "mistralai/", "fireworks/", "together/", "groq/", "cohere/",
)
_DATE_OR_TAG_SUFFIX_RE = re.compile(
    r"(?:@[\w.-]+|:[\w.-]+|-(?:20\d{2}-?\d{2}-?\d{2}|\d{8}|latest|preview(?:-\d{2}-\d{2})?|exp(?:-\d{2}-\d{2})?|beta|alpha|thinking|instruct|chat-latest|001|002|003))+\Z"
)
_CANONICAL_ALIAS_RE = re.compile(r"[^a-z0-9]+")
_VARIANT_TOKENS = frozenset({
    "mini", "nano", "lite", "flash", "pro", "ultra", "turbo", "max", "plus", "air",
    "coder", "vl", "vision", "audio", "realtime", "tts", "stt", "embed", "embedding",
    "rerank", "haiku", "sonnet", "opus", "reasoner", "r1", "distill", "free", "fast",
    "scout", "maverick",
    # Relay billing-mode and distinct-capability SKU qualifiers
    "as", "once", "percall", "call", "times",
    "image", "img", "search", "web", "online", "net",
    "video", "music", "edit", "upscale", "inpainting",
    "enterprise", "high", "low", "medium", "deep", "research", "live",
})
_BRACKET_TAG_RE = re.compile(r"^[\[\uFF3B]([^\]\uFF3D]{1,48})[\]\uFF3D]\s*")
_PER_CALL_BRACKET_RE = re.compile(r"(?:^|[\s/_-])(?:次|按次|once|percall|per-call|as)(?:$|[\s/_-])")


def _slug_key(text: str) -> str:
    """Normalize dots/underscores/hyphens into a single dash-separated slug."""
    return _CANONICAL_ALIAS_RE.sub("-", text.lower()).strip("-")


def _strip_vendor_prefix(raw: object) -> str:
    if type(raw) is not str:
        return ""
    value = raw.strip().lower()
    if not value:
        return ""
    is_per_call_bracket = False
    while True:
        match = _BRACKET_TAG_RE.match(value)
        if match is None:
            break
        tag = match.group(1).strip()
        if _PER_CALL_BRACKET_RE.search(tag):
            is_per_call_bracket = True
        value = value[match.end():]
    changed = True
    while changed:
        changed = False
        for prefix in _VENDOR_PREFIXES:
            if value.startswith(prefix):
                value = value[len(prefix):]
                changed = True
    if "/" in value:
        tail = value.rsplit("/", 1)[-1].strip()
        if tail:
            value = tail
    if is_per_call_bracket and value and not value.startswith("percall-"):
        value = f"percall-{value}"
    return value


def _normalize_model_name(raw: object) -> str:
    value = _strip_vendor_prefix(raw)
    if not value:
        return ""
    for _ in range(3):
        stripped = _DATE_OR_TAG_SUFFIX_RE.sub("", value)
        if stripped == value or not stripped:
            break
        value = stripped
    return value


_PRESETS_BY_SLUG: dict[str, PresetTariff] = {}
for _item in _BUILTIN_PRESETS:
    _PRESETS_BY_SLUG[_slug_key(_item.preset_id)] = _item
_SORTED_PRESET_SLUGS: tuple[tuple[str, PresetTariff], ...] = tuple(
    sorted(_PRESETS_BY_SLUG.items(), key=lambda pair: len(pair[0]), reverse=True)
)


def match_builtin_tariff(model: object) -> PresetTariff | None:
    """Match a raw model name against built-in reference tariffs on clean token boundaries."""
    normalized = _normalize_model_name(model)
    if not normalized:
        return None
    slug = _slug_key(normalized)
    if not slug:
        return None
    exact = _PRESETS_BY_SLUG.get(slug)
    if exact is not None:
        return exact
    raw_slug = _slug_key(str(model).strip().rsplit("/", 1)[-1])
    if raw_slug in _PRESETS_BY_SLUG:
        return _PRESETS_BY_SLUG[raw_slug]
    for candidate_slug, preset in _SORTED_PRESET_SLUGS:
        if slug.startswith(candidate_slug + "-"):
            return preset
    return None


def catalog_presets() -> list[dict[str, Any]]:
    """Return built-in model presets in USD per 1M tokens for UI display/autofill."""
    return [item.to_usd_dict() for item in _BUILTIN_PRESETS]


@dataclass(frozen=True)
class CatalogPrice:
    price_key: str
    source_id: str
    source_label: str
    model_id: str
    vendor: str
    display_name: str
    rule: dict[str, Any]
    context_window: int | None = None
    max_output_tokens: int | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_key": self.price_key,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "model_id": self.model_id,
            "vendor": self.vendor,
            "display_name": self.display_name,
            "rule": deepcopy(self.rule),
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CatalogPrice":
        return cls(
            price_key=str(raw.get("price_key") or ""),
            source_id=str(raw.get("source_id") or ""),
            source_label=str(raw.get("source_label") or raw.get("source_id") or ""),
            model_id=str(raw.get("model_id") or ""),
            vendor=str(raw.get("vendor") or ""),
            display_name=str(raw.get("display_name") or raw.get("model_id") or ""),
            rule=deepcopy(raw.get("rule") or {}),
            context_window=(int(raw["context_window"]) if type(raw.get("context_window")) is int else None),
            max_output_tokens=(int(raw["max_output_tokens"]) if type(raw.get("max_output_tokens")) is int else None),
            updated_at=str(raw["updated_at"]) if type(raw.get("updated_at")) is str else None,
        )


@dataclass(frozen=True)
class SourceStatus:
    source_id: str
    kind: str  # "public" | "newapi"
    label: str
    url: str
    enabled: bool = True
    status: str = "pending"  # "ok" | "error" | "pending" | "disabled"
    model_count: int = 0
    last_sync_at: str | None = None
    last_ok_at: str | None = None
    etag: str | None = None
    error: str | None = None
    provider_id: str | None = None
    needs_key: bool = False
    currency: str | None = None
    currency_override: str | None = None
    auth_mode: str | None = None
    site_meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source_id": self.source_id,
            "kind": self.kind,
            "label": self.label,
            "url": self.url,
            "enabled": self.enabled,
            "status": self.status,
            "model_count": self.model_count,
            "last_sync_at": self.last_sync_at,
            "last_ok_at": self.last_ok_at,
            "etag": self.etag,
            "error": self.error,
            "provider_id": self.provider_id,
            "needs_key": self.needs_key,
        }
        if self.currency is not None:
            out["currency"] = self.currency
        if self.currency_override is not None:
            out["currency_override"] = self.currency_override
        if self.auth_mode is not None:
            out["auth_mode"] = self.auth_mode
        if self.site_meta is not None:
            out["site_meta"] = deepcopy(self.site_meta)
        return out


@dataclass(frozen=True)
class Candidate:
    price_key: str
    source_id: str
    source_label: str
    model_id: str
    vendor: str
    display_name: str
    score: float
    is_own_gateway: bool
    rule: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "price_key": self.price_key,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "model_id": self.model_id,
            "vendor": self.vendor,
            "display_name": self.display_name,
            "score": round(self.score, 4),
            "is_own_gateway": self.is_own_gateway,
            "rule": deepcopy(self.rule),
        }


def _nonneg_float(value: object, *, scale: float = 1.0, max_val: float = MAX_PRICE_PER_MILLION) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if type(value) in (int, float):
        num = float(value)
    elif type(value) is str:
        try:
            num = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(num) or num < 0.0:
        return None
    scaled = num * scale
    if not math.isfinite(scaled) or scaled > max_val:
        return None
    return round(scaled, 6)


def _pos_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if type(value) is int and value > 0:
        return value
    if type(value) is float and math.isfinite(value) and value > 0:
        return int(value)
    return None


def _make_per_token_rule(
    inp: float | None,
    cached: float | None,
    creation: float | None,
    out: float | None,
    *,
    currency: str = "USD",
) -> dict[str, Any]:
    norm_cur = "CNY" if currency == "CNY" else "USD"
    return {
        "mode": "per_token",
        "currency": norm_cur,
        "input": inp,
        "input_cached": cached,
        "cache_creation": creation,
        "output": out,
        "multiplier": 1.0,
        "free": (
            inp == 0.0
            and out == 0.0
            and (cached is None or cached == 0.0)
            and (creation is None or creation == 0.0)
        ),
        "configured": {
            "input": inp is not None,
            "input_cached": cached is not None,
            "cache_creation": creation is not None,
            "output": out is not None,
        },
    }


def _make_per_tier_rule(
    base: dict[str, float | None],
    context_tiers: list[dict[str, Any]],
    service_tiers: list[dict[str, Any]],
    *,
    currency: str = "USD",
) -> dict[str, Any]:
    norm_cur = "CNY" if currency == "CNY" else "USD"
    sorted_ctx = sorted(context_tiers, key=lambda item: int(item["threshold_tokens"]))
    return {
        "mode": "per_tier",
        "currency": norm_cur,
        "base": {
            "input": base.get("input"),
            "input_cached": base.get("input_cached"),
            "cache_creation": base.get("cache_creation"),
            "output": base.get("output"),
        },
        "context_tiers": sorted_ctx,
        "service_tiers": service_tiers,
        "multiplier": 1.0,
    }


def adapt_modelsdev(payload: object, *, updated_at: str | None = None) -> list[CatalogPrice]:
    """Adapt ``https://models.dev/catalog.json`` (USD/1M + context tiers + fast priority tier)."""
    if type(payload) is not dict:
        return []
    root = payload.get("providers") if isinstance(payload.get("providers"), dict) else payload
    results: list[CatalogPrice] = []
    seen_keys: set[str] = set()

    for provider_slug, provider_obj in root.items():
        if type(provider_slug) is not str or type(provider_obj) is not dict:
            continue
        models = provider_obj.get("models")
        if type(models) is not dict:
            continue
        vendor_name = str(provider_obj.get("name") or provider_slug).strip()
        for model_key, model_obj in models.items():
            if type(model_key) is not str or type(model_obj) is not dict:
                continue
            cost = model_obj.get("cost")
            if type(cost) is not dict:
                continue
            inp = _nonneg_float(cost.get("input"))
            out = _nonneg_float(cost.get("output"))
            if inp is None and out is None:
                continue
            inp = 0.0 if inp is None else inp
            out = 0.0 if out is None else out
            cached = _nonneg_float(cost.get("cache_read"))
            creation = _nonneg_float(cost.get("cache_write"))

            context_tiers: list[dict[str, Any]] = []
            over_200k = cost.get("context_over_200k")
            if type(over_200k) is dict:
                t_in = _nonneg_float(over_200k.get("input"))
                t_out = _nonneg_float(over_200k.get("output"))
                t_cached = _nonneg_float(over_200k.get("cache_read"))
                t_create = _nonneg_float(over_200k.get("cache_write"))
                if any(v is not None for v in (t_in, t_out, t_cached, t_create)):
                    context_tiers.append({
                        "threshold_tokens": 200_000,
                        "input": t_in,
                        "input_cached": t_cached,
                        "cache_creation": t_create,
                        "output": t_out,
                    })
            raw_ctx_tiers = cost.get("context_tiers")
            if type(raw_ctx_tiers) is list:
                for tier in raw_ctx_tiers:
                    if type(tier) is not dict:
                        continue
                    threshold = _pos_int(
                        tier.get("threshold_tokens") or tier.get("threshold") or tier.get("min_tokens")
                    )
                    if threshold is None:
                        continue
                    t_in = _nonneg_float(tier.get("input"))
                    t_out = _nonneg_float(tier.get("output"))
                    t_cached = _nonneg_float(tier.get("input_cached", tier.get("cache_read")))
                    t_create = _nonneg_float(tier.get("cache_creation", tier.get("cache_write")))
                    if any(v is not None for v in (t_in, t_out, t_cached, t_create)):
                        context_tiers.append({
                            "threshold_tokens": threshold,
                            "input": t_in,
                            "input_cached": t_cached,
                            "cache_creation": t_create,
                            "output": t_out,
                        })

            service_tiers: list[dict[str, Any]] = []
            experimental = model_obj.get("experimental")
            modes = experimental.get("modes") if type(experimental) is dict else None
            fast_mode = modes.get("fast") if type(modes) is dict else None
            if fast_mode is not None:
                st_entry = _parse_modelsdev_fast_mode(fast_mode, base_in=inp, base_cached=cached,
                                                      base_create=creation, base_out=out)
                if st_entry is not None:
                    service_tiers.append(st_entry)

            if context_tiers or service_tiers:
                rule = _make_per_tier_rule(
                    {"input": inp, "input_cached": cached, "cache_creation": creation, "output": out},
                    context_tiers,
                    service_tiers,
                )
            else:
                rule = _make_per_token_rule(inp, cached, creation, out)

            model_id = str(model_obj.get("id") or model_key).strip()
            if not model_id:
                continue
            price_key = (
                f"modelsdev:{model_id}"
                if "/" in model_id
                else f"modelsdev:{provider_slug}/{model_id}"
            )
            if price_key in seen_keys:
                continue
            seen_keys.add(price_key)
            limit = model_obj.get("limit") if type(model_obj.get("limit")) is dict else {}
            results.append(CatalogPrice(
                price_key=price_key,
                source_id="modelsdev",
                source_label="models.dev",
                model_id=model_id,
                vendor=vendor_name,
                display_name=str(model_obj.get("name") or model_id).strip(),
                rule=rule,
                context_window=_pos_int(limit.get("context")),
                max_output_tokens=_pos_int(limit.get("output")),
                updated_at=updated_at,
            ))
    return results


def _parse_modelsdev_fast_mode(
    fast_mode: object,
    *,
    base_in: float,
    base_cached: float | None,
    base_create: float | None,
    base_out: float,
) -> dict[str, Any] | None:
    if type(fast_mode) in (int, float) and not isinstance(fast_mode, bool):
        mult = _nonneg_float(fast_mode, max_val=100.0)
        if mult is None:
            return None
        return {
            "match": "priority",
            "input_multiplier": mult,
            "input_cached_multiplier": mult,
            "cache_creation_multiplier": mult,
            "output_multiplier": mult,
            "input": None,
            "input_cached": None,
            "cache_creation": None,
            "output": None,
        }
    if type(fast_mode) is not dict:
        return None
    fast_cost = fast_mode.get("cost") if type(fast_mode.get("cost")) is dict else fast_mode
    mult_all = _nonneg_float(fast_cost.get("multiplier"), max_val=100.0)
    if mult_all is not None:
        return {
            "match": "priority",
            "input_multiplier": mult_all,
            "input_cached_multiplier": mult_all,
            "cache_creation_multiplier": mult_all,
            "output_multiplier": mult_all,
            "input": None,
            "input_cached": None,
            "cache_creation": None,
            "output": None,
        }

    f_in = _nonneg_float(fast_cost.get("input"))
    f_out = _nonneg_float(fast_cost.get("output"))
    f_cached = _nonneg_float(fast_cost.get("cache_read", fast_cost.get("input_cached")))
    f_create = _nonneg_float(fast_cost.get("cache_write", fast_cost.get("cache_creation")))
    m_in = _nonneg_float(fast_cost.get("input_multiplier"), max_val=100.0)
    m_out = _nonneg_float(fast_cost.get("output_multiplier"), max_val=100.0)
    m_cached = _nonneg_float(fast_cost.get("input_cached_multiplier"), max_val=100.0)
    m_create = _nonneg_float(fast_cost.get("cache_creation_multiplier"), max_val=100.0)

    if m_in is None and f_in is not None and base_in > 0:
        m_in = round(f_in / base_in, 6)
    if m_out is None and f_out is not None and base_out > 0:
        m_out = round(f_out / base_out, 6)
    if m_cached is None and f_cached is not None and base_cached is not None and base_cached > 0:
        m_cached = round(f_cached / base_cached, 6)
    if m_create is None and f_create is not None and base_create is not None and base_create > 0:
        m_create = round(f_create / base_create, 6)

    if not any(v is not None for v in (f_in, f_out, f_cached, f_create, m_in, m_out, m_cached, m_create)):
        return None
    return {
        "match": "priority",
        "input_multiplier": m_in,
        "input_cached_multiplier": m_cached,
        "cache_creation_multiplier": m_create,
        "output_multiplier": m_out,
        "input": f_in,
        "input_cached": f_cached,
        "cache_creation": f_create,
        "output": f_out,
    }


def adapt_litellm(payload: object, *, updated_at: str | None = None) -> list[CatalogPrice]:
    """Adapt LiteLLM ``model_prices_and_context_window.json`` (per-token USD * 1e6)."""
    if type(payload) is not dict:
        return []
    results: list[CatalogPrice] = []
    allowed_modes = {"", "chat", "responses", "completion"}

    for model_key, entry in payload.items():
        if type(model_key) is not str or model_key == "sample_spec" or type(entry) is not dict:
            continue
        mode_val = str(entry.get("mode") or "").strip().lower()
        if mode_val not in allowed_modes:
            continue

        inp = _nonneg_float(entry.get("input_cost_per_token"), scale=1_000_000.0)
        out = _nonneg_float(entry.get("output_cost_per_token"), scale=1_000_000.0)
        if inp is None and out is None:
            continue
        inp = 0.0 if inp is None else inp
        out = 0.0 if out is None else out
        cached = _nonneg_float(entry.get("cache_read_input_token_cost"), scale=1_000_000.0)
        creation = _nonneg_float(entry.get("cache_creation_input_token_cost"), scale=1_000_000.0)

        context_tiers: list[dict[str, Any]] = []
        for suffix, threshold in (
            ("above_128k_tokens", 128_000),
            ("above_200k_tokens", 200_000),
            ("above_256k_tokens", 256_000),
            ("above_1m_tokens", 1_000_000),
        ):
            t_in = _nonneg_float(entry.get(f"input_cost_per_token_{suffix}"), scale=1_000_000.0)
            t_out = _nonneg_float(entry.get(f"output_cost_per_token_{suffix}"), scale=1_000_000.0)
            t_cached = _nonneg_float(entry.get(f"cache_read_input_token_cost_{suffix}"), scale=1_000_000.0)
            t_create = _nonneg_float(entry.get(f"cache_creation_input_token_cost_{suffix}"), scale=1_000_000.0)
            if any(v is not None for v in (t_in, t_out, t_cached, t_create)):
                context_tiers.append({
                    "threshold_tokens": threshold,
                    "input": t_in,
                    "input_cached": t_cached,
                    "cache_creation": t_create,
                    "output": t_out,
                })

        service_tiers: list[dict[str, Any]] = []
        for tier_name in ("priority", "flex"):
            s_in = _nonneg_float(entry.get(f"input_cost_per_token_{tier_name}"), scale=1_000_000.0)
            s_out = _nonneg_float(entry.get(f"output_cost_per_token_{tier_name}"), scale=1_000_000.0)
            s_cached = _nonneg_float(entry.get(f"cache_read_input_token_cost_{tier_name}"), scale=1_000_000.0)
            s_create = _nonneg_float(entry.get(f"cache_creation_input_token_cost_{tier_name}"), scale=1_000_000.0)
            if any(v is not None for v in (s_in, s_out, s_cached, s_create)):
                service_tiers.append({
                    "match": tier_name,
                    "input_multiplier": round(s_in / inp, 6) if (s_in is not None and inp > 0) else None,
                    "input_cached_multiplier": (
                        round(s_cached / cached, 6) if (s_cached is not None and cached is not None and cached > 0) else None
                    ),
                    "cache_creation_multiplier": (
                        round(s_create / creation, 6) if (s_create is not None and creation is not None and creation > 0) else None
                    ),
                    "output_multiplier": round(s_out / out, 6) if (s_out is not None and out > 0) else None,
                    "input": s_in,
                    "input_cached": s_cached,
                    "cache_creation": s_create,
                    "output": s_out,
                })

        if context_tiers or service_tiers:
            rule = _make_per_tier_rule(
                {"input": inp, "input_cached": cached, "cache_creation": creation, "output": out},
                context_tiers,
                service_tiers,
            )
        else:
            rule = _make_per_token_rule(inp, cached, creation, out)

        vendor = str(entry.get("litellm_provider") or "").strip()
        if not vendor and "/" in model_key:
            vendor = model_key.split("/", 1)[0]
        results.append(CatalogPrice(
            price_key=f"litellm:{model_key}",
            source_id="litellm",
            source_label="LiteLLM",
            model_id=model_key,
            vendor=vendor,
            display_name=model_key,
            rule=rule,
            context_window=_pos_int(entry.get("max_input_tokens") or entry.get("max_tokens")),
            max_output_tokens=_pos_int(entry.get("max_output_tokens")),
            updated_at=updated_at,
        ))
    return results


def adapt_openrouter(payload: object, *, updated_at: str | None = None) -> list[CatalogPrice]:
    """Adapt ``https://openrouter.ai/api/v1/models`` (per-token USD string * 1e6)."""
    if type(payload) is dict:
        items = payload.get("data")
    elif type(payload) is list:
        items = payload
    else:
        return []
    if type(items) is not list:
        return []

    results: list[CatalogPrice] = []
    seen_keys: set[str] = set()
    for item in items:
        if type(item) is not dict:
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        pricing = item.get("pricing")
        if type(pricing) is not dict:
            continue
        inp = _nonneg_float(pricing.get("prompt"), scale=1_000_000.0)
        out = _nonneg_float(pricing.get("completion"), scale=1_000_000.0)
        cached = _nonneg_float(pricing.get("input_cache_read"), scale=1_000_000.0)
        creation = _nonneg_float(pricing.get("input_cache_write"), scale=1_000_000.0)
        req_price = _nonneg_float(pricing.get("request"), scale=1.0)

        if inp is None and out is None:
            if req_price is not None and req_price > 0:
                rule: dict[str, Any] = {
                    "mode": "per_turn",
                    "currency": "USD",
                    "price": req_price,
                    "multiplier": 1.0,
                }
            else:
                continue
        else:
            inp_v = 0.0 if inp is None else inp
            out_v = 0.0 if out is None else out
            if inp_v == 0.0 and out_v == 0.0 and req_price is not None and req_price > 0:
                rule = {
                    "mode": "per_turn",
                    "currency": "USD",
                    "price": req_price,
                    "multiplier": 1.0,
                }
            else:
                rule = _make_per_token_rule(inp_v, cached, creation, out_v)

        price_key = f"openrouter:{model_id}"
        if price_key in seen_keys:
            continue
        seen_keys.add(price_key)
        vendor = model_id.split("/", 1)[0] if "/" in model_id else "openrouter"
        top_provider = item.get("top_provider") if type(item.get("top_provider")) is dict else {}
        results.append(CatalogPrice(
            price_key=price_key,
            source_id="openrouter",
            source_label="OpenRouter",
            model_id=model_id,
            vendor=vendor,
            display_name=str(item.get("name") or model_id).strip(),
            rule=rule,
            context_window=_pos_int(item.get("context_length")),
            max_output_tokens=_pos_int(top_provider.get("max_completion_tokens")),
            updated_at=updated_at,
        ))
    return results


def _pos_float(value: object, *, min_val: float = 0.0, max_val: float = 1e12) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if type(value) in (int, float):
        num = float(value)
    elif type(value) is str:
        try:
            num = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(num) or num <= min_val or num > max_val:
        return None
    return num


def _has_cjk_text(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    return any("\u4e00" <= ch <= "\u9fff" for ch in value)


def _is_non_usd_custom_symbol(symbol: object) -> bool:
    if type(symbol) is not str:
        return False
    clean = symbol.strip()
    if not clean or clean in _NON_CUSTOM_CURRENCY_SYMBOLS:
        return False
    return any(ord(ch) > 127 for ch in clean)


def _has_chinese_site_signal(
    *,
    system_name: object = None,
    custom_symbol: object = None,
    status_data: dict[str, Any] | None = None,
    pricing_meta: dict[str, Any] | None = None,
) -> bool:
    if _has_cjk_text(system_name) or _has_cjk_text(custom_symbol):
        return True
    if isinstance(status_data, dict):
        for key in ("scrolling_announcement", "footer_html", "home_page_title_primary", "home_page_title_secondary"):
            if _has_cjk_text(status_data.get(key)):
                return True
    if isinstance(pricing_meta, dict):
        for field in ("group_ratio", "usable_group"):
            mapping = pricing_meta.get(field)
            if isinstance(mapping, dict):
                for gk, gv in mapping.items():
                    if _has_cjk_text(gk) or _has_cjk_text(gv):
                        return True
    return False


def parse_newapi_topup_info(payload: object) -> dict[str, Any] | None:
    """Parse sanitized New API ``GET /api/user/topup/info`` into currency confirmation signals.

    Strictly discards any URLs, merchant IDs, keys, or personal metadata.
    """
    if type(payload) is not dict:
        return None
    if payload.get("success") is False:
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if type(data) is not dict:
        return None

    enable_online = bool(data.get("enable_online_topup"))
    enable_stripe = bool(data.get("enable_stripe_topup"))
    enable_creem = bool(data.get("enable_creem_topup"))

    raw_methods = data.get("pay_methods")
    pay_method_types: list[str] = []
    has_rmb_pay = False
    if isinstance(raw_methods, list):
        for item in raw_methods[:16]:
            if type(item) is dict:
                ptype = str(item.get("type") or "").strip().lower()[:32]
                pname = str(item.get("name") or "").strip()[:32]
            elif type(item) is str:
                ptype = item.strip().lower()[:32]
                pname = ptype
            else:
                continue
            if ptype and ptype not in pay_method_types:
                pay_method_types.append(ptype)
            if ptype in _RMB_PAY_METHOD_TYPES or any(w in pname for w in ("支付宝", "微信")):
                has_rmb_pay = True

    rmb_topup_confirmed = bool(
        (enable_online or bool(pay_method_types))
        and has_rmb_pay
        and not enable_stripe
        and not enable_creem
    )

    min_topup = _pos_float(data.get("min_topup"), min_val=0.0, max_val=1_000_000.0)
    discount_tiers: dict[str, float] = {}
    raw_discount = data.get("discount")
    if isinstance(raw_discount, dict):
        for raw_k, raw_v in list(raw_discount.items())[:16]:
            kstr = str(raw_k).strip()[:16]
            val = _pos_float(raw_v, min_val=0.0, max_val=2.0)
            if kstr and val is not None:
                discount_tiers[kstr] = round(val, 6)

    return {
        "enable_online_topup": enable_online,
        "enable_stripe_topup": enable_stripe,
        "enable_creem_topup": enable_creem,
        "pay_method_types": pay_method_types,
        "rmb_topup_confirmed": rmb_topup_confirmed,
        "min_topup": round(min_topup, 6) if min_topup is not None else None,
        "discount_tiers": discount_tiers,
    }


def _normalize_newapi_display_type(raw_display_type: object) -> str:
    qdt = raw_display_type.strip().upper() if type(raw_display_type) is str else ""
    if qdt in ("POINTS", "POINT", "CREDIT"):
        return "CUSTOM"
    if qdt in ("CNY", "USD", "CUSTOM", "TOKENS"):
        return qdt
    return ""


def parse_newapi_status(
    payload: object,
    *,
    topup_info: dict[str, Any] | None = None,
    pricing_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Parse New API / One API ``GET /api/status`` into normalized site & currency metadata.

    Implements 4-level anti-false-positive currency detection:
    - Level 1 (``CNY``): strictly ``CNY`` scaled by ``usd_exchange_rate`` (never multiplies ``price``).
    - Level 2 (``CUSTOM`` / ``POINTS`` / ``CREDIT``): uses authenticated ``/api/user/topup/info``
      RMB confirmation when available, else infers ``CNY`` at ``unit_scale = price`` when
      ``0 < price < 4.5`` together with a non-USD custom symbol or Chinese site signal; never
      multiplies by ``custom_currency_exchange_rate`` or ``usd_exchange_rate``.
    - Level 3 (``USD`` or absent): strictly defaults to ``USD`` (``unit_scale = 1.0``) so genuine
      USD relays are never misclassified; attaches a non-destructive ``cny_recharge_hint`` when
      ``0 < price < 4.5`` plus Chinese/RMB payment signals are present.
    """
    if type(payload) is not dict:
        return {
            "schema_version": NEWAPI_SITE_META_SCHEMA_VERSION,
            "system_name": "",
            "quota_display_type": "",
            "display_in_currency": True,
            "quota_per_unit": DEFAULT_NEWAPI_QUOTA_PER_UNIT,
            "usd_exchange_rate": 1.0,
            "custom_currency_symbol": "",
            "custom_currency_exchange_rate": None,
            "recharge_price": None,
            "site_recharge_price": None,
            "currency": "USD",
            "unit_scale": 1.0,
            "currency_source": "default_usd",
            "topup_rmb_confirmed": False,
            "cny_recharge_hint": None,
        }
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw_name = data.get("system_name")
    system_name = raw_name.strip()[:80] if type(raw_name) is str else ""

    qdt = _normalize_newapi_display_type(data.get("quota_display_type"))

    display_in_currency = bool(data.get("display_in_currency", True))
    quota_per_unit = _pos_float(data.get("quota_per_unit"), min_val=0.0, max_val=1e12) or DEFAULT_NEWAPI_QUOTA_PER_UNIT
    usd_exchange_rate = _pos_float(data.get("usd_exchange_rate"), min_val=0.0, max_val=1000.0) or 1.0
    raw_custom_sym = data.get("custom_currency_symbol")
    custom_symbol = raw_custom_sym.strip()[:12] if type(raw_custom_sym) is str else ""
    custom_rate = _pos_float(data.get("custom_currency_exchange_rate"), min_val=0.0, max_val=100_000.0)
    recharge_price = _pos_float(data.get("price"), min_val=0.0, max_val=100_000.0)
    rounded_recharge = round(recharge_price, 6) if recharge_price is not None else None

    parsed_topup = topup_info if isinstance(topup_info, dict) else {}
    topup_rmb_confirmed = bool(parsed_topup.get("rmb_topup_confirmed"))
    has_cn_signal = _has_chinese_site_signal(
        system_name=system_name,
        custom_symbol=custom_symbol,
        status_data=data,
        pricing_meta=pricing_meta,
    )
    has_non_usd_sym = _is_non_usd_custom_symbol(custom_symbol)
    low_rmb_price = (
        recharge_price is not None
        and 0.0 < recharge_price < _RMB_RECHARGE_PRICE_UPPER_BOUND
    )
    cny_recharge_hint: dict[str, Any] | None = None

    if qdt == "CNY":
        # Level 1: explicit CNY display — scale ONLY by usd_exchange_rate (never price!).
        currency = "CNY"
        unit_scale = round(usd_exchange_rate, 6)
        currency_source = "status_cny"
    elif qdt == "CUSTOM":
        # Level 2: virtual/custom unit — prefer authenticated topup confirmation, then low RMB price signal.
        if topup_rmb_confirmed:
            currency = "CNY"
            unit_scale = rounded_recharge if rounded_recharge is not None else 1.0
            currency_source = "topup_custom_cny"
        elif low_rmb_price and (has_non_usd_sym or has_cn_signal):
            currency = "CNY"
            unit_scale = rounded_recharge if rounded_recharge is not None else 1.0
            currency_source = "status_custom_cny"
        else:
            currency = "USD"
            unit_scale = 1.0
            currency_source = "status_custom"
    elif qdt == "USD":
        # Level 3: explicit USD — never silently override genuine USD sites; surface gentle hint if RMB topup detected.
        currency = "USD"
        unit_scale = 1.0
        currency_source = "status_usd"
        if low_rmb_price and (topup_rmb_confirmed or has_cn_signal):
            cny_recharge_hint = {
                "recharge_price": rounded_recharge,
                "suggested_currency": "CNY",
            }
    elif qdt == "TOKENS":
        currency = "USD"
        unit_scale = 1.0
        currency_source = "status_tokens"
    else:
        currency = "USD"
        unit_scale = 1.0
        currency_source = "default_usd"
        if low_rmb_price and (topup_rmb_confirmed or has_cn_signal):
            cny_recharge_hint = {
                "recharge_price": rounded_recharge,
                "suggested_currency": "CNY",
            }

    return {
        "schema_version": NEWAPI_SITE_META_SCHEMA_VERSION,
        "system_name": system_name,
        "quota_display_type": qdt,
        "display_in_currency": display_in_currency,
        "quota_per_unit": quota_per_unit,
        "usd_exchange_rate": round(usd_exchange_rate, 6),
        "custom_currency_symbol": custom_symbol,
        "custom_currency_exchange_rate": round(custom_rate, 6) if custom_rate is not None else None,
        "recharge_price": rounded_recharge,
        "site_recharge_price": rounded_recharge,
        "currency": currency,
        "unit_scale": unit_scale,
        "currency_source": currency_source,
        "topup_rmb_confirmed": topup_rmb_confirmed,
        "cny_recharge_hint": cny_recharge_hint,
        "has_chinese_signal": has_cn_signal,
    }


def resolve_newapi_currency_params(
    site_meta: dict[str, Any] | None,
    *,
    currency_override: str | None = None,
    fiat_per_base_unit_override: float | None = None,
) -> tuple[str, float, float]:
    """Return ``(currency, unit_scale, quota_per_unit)`` honoring 4-level rules and user overrides."""
    meta = site_meta if isinstance(site_meta, dict) else {}
    qpu = _pos_float(meta.get("quota_per_unit"), min_val=0.0, max_val=1e12) or DEFAULT_NEWAPI_QUOTA_PER_UNIT
    qdt = _normalize_newapi_display_type(meta.get("quota_display_type"))
    fx = _pos_float(meta.get("usd_exchange_rate"), min_val=0.0, max_val=1000.0) or 1.0
    recharge_price = _pos_float(
        meta.get("site_recharge_price") if meta.get("site_recharge_price") is not None else meta.get("recharge_price"),
        min_val=0.0,
        max_val=100_000.0,
    )
    low_rmb_price = (
        recharge_price is not None
        and 0.0 < recharge_price < _RMB_RECHARGE_PRICE_UPPER_BOUND
    )
    explicit_fiat_rate = _pos_float(fiat_per_base_unit_override, min_val=0.0, max_val=100_000.0)
    quota_fiat_rate = _pos_float(meta.get("quota_fiat_per_base_unit"), min_val=0.0, max_val=100_000.0)
    effective_custom_rate = explicit_fiat_rate if explicit_fiat_rate is not None else quota_fiat_rate

    override = str(currency_override or "").strip().upper()
    if override == "CNY":
        if explicit_fiat_rate is not None:
            return "CNY", round(explicit_fiat_rate, 6), qpu
        if qdt == "CNY":
            return "CNY", round(fx, 6), qpu
        if qdt == "CUSTOM":
            if effective_custom_rate is not None:
                return "CNY", round(effective_custom_rate, 6), qpu
            if low_rmb_price and recharge_price is not None:
                return "CNY", round(recharge_price, 6), qpu
            return "CNY", 1.0, qpu
        return "CNY", 1.0, qpu
    if override == "USD":
        if explicit_fiat_rate is not None:
            return "USD", round(explicit_fiat_rate, 6), qpu
        return "USD", 1.0, qpu

    # Auto mode — Level 1: explicit CNY
    if qdt == "CNY":
        if explicit_fiat_rate is not None:
            return "CNY", round(explicit_fiat_rate, 6), qpu
        return "CNY", round(fx, 6), qpu

    # Level 2: CUSTOM / POINTS / CREDIT
    if qdt == "CUSTOM":
        quota_fiat_cur = str(meta.get("quota_fiat_currency") or "").strip().upper()
        if quota_fiat_cur in ("CNY", "USD"):
            if quota_fiat_cur == "USD":
                if explicit_fiat_rate is not None:
                    scale = explicit_fiat_rate
                elif quota_fiat_rate is not None and not (
                    recharge_price is not None
                    and math.isclose(quota_fiat_rate, recharge_price, rel_tol=1e-6, abs_tol=1e-6)
                ):
                    scale = quota_fiat_rate
                else:
                    scale = 1.0
            else:
                scale = (
                    effective_custom_rate
                    if effective_custom_rate is not None
                    else (recharge_price if recharge_price is not None else 1.0)
                )
            return quota_fiat_cur, round(scale, 6), qpu

        cur_src = str(meta.get("currency_source") or "").strip()
        topup_rmb = bool(meta.get("topup_rmb_confirmed"))
        has_non_usd_sym = _is_non_usd_custom_symbol(meta.get("custom_currency_symbol"))
        has_cn_sig = bool(meta.get("has_chinese_signal")) or _has_chinese_site_signal(
            system_name=meta.get("system_name"),
            custom_symbol=meta.get("custom_currency_symbol"),
            pricing_meta=meta,
        )
        if topup_rmb or cur_src in ("topup_custom_cny", "status_custom_cny") or (
            low_rmb_price and (has_non_usd_sym or has_cn_sig)
        ):
            scale = (
                effective_custom_rate
                if effective_custom_rate is not None
                else (recharge_price if recharge_price is not None else 1.0)
            )
            return "CNY", round(scale, 6), qpu

        if explicit_fiat_rate is not None:
            fallback_cur = "CNY" if meta.get("currency") == "CNY" else "USD"
            return fallback_cur, round(explicit_fiat_rate, 6), qpu
        return "USD", 1.0, qpu

    # Level 3: USD / TOKENS / absent — never silently reclassify genuine USD relays
    if explicit_fiat_rate is not None:
        return "USD", round(explicit_fiat_rate, 6), qpu
    return "USD", 1.0, qpu


def extract_newapi_pricing_meta(payload: object) -> dict[str, Any]:
    """Extract sanitized ``group_ratio``, ``usable_group``, and ``auto_groups`` from ``/api/pricing``."""
    if type(payload) is not dict:
        return {"group_ratio": {}, "usable_group": {}, "auto_groups": []}

    raw_ratios = payload.get("group_ratio")
    group_ratio: dict[str, float] = {}
    if isinstance(raw_ratios, dict):
        for raw_group, raw_val in raw_ratios.items():
            if len(group_ratio) >= 128:
                break
            if type(raw_group) is not str:
                continue
            gname = raw_group.strip()[:64]
            if not gname:
                continue
            ratio = _nonneg_float(raw_val, scale=1.0, max_val=1000.0)
            if ratio is not None:
                group_ratio[gname] = ratio

    raw_usable = payload.get("usable_group")
    usable_group: dict[str, str] = {}
    if isinstance(raw_usable, dict):
        for raw_group, raw_desc in raw_usable.items():
            if len(usable_group) >= 128:
                break
            if type(raw_group) is not str:
                continue
            gname = raw_group.strip()[:64]
            if not gname:
                continue
            desc = raw_desc.strip()[:120] if type(raw_desc) is str else ""
            if desc:
                usable_group[gname] = desc

    raw_auto = payload.get("auto_groups")
    auto_groups: list[str] = []
    if isinstance(raw_auto, list):
        for item in raw_auto[:32]:
            if type(item) is str and item.strip():
                auto_groups.append(item.strip()[:64])

    return {
        "group_ratio": group_ratio,
        "usable_group": usable_group,
        "auto_groups": auto_groups,
    }


def infer_newapi_cluster_group(
    pricing_payload: object,
    available_models: set[str] | list[str] | tuple[str, ...],
) -> dict[str, Any] | None:
    """Infer the likely New API group and ``group_ratio`` for a key from its ``/v1/models`` set."""
    if type(pricing_payload) is not dict or not available_models:
        return None
    items = pricing_payload.get("data")
    if type(items) is not list:
        return None
    meta = extract_newapi_pricing_meta(pricing_payload)
    group_ratio = meta["group_ratio"]
    if not group_ratio:
        return None

    model_set = {str(m).strip() for m in available_models if type(m) is str and m.strip()}
    if not model_set:
        return None

    group_models: dict[str, set[str]] = {}
    for item in items:
        if type(item) is not dict:
            continue
        mname = str(item.get("model_name") or "").strip()
        if not mname:
            continue
        groups = item.get("enable_groups")
        if type(groups) is not list:
            continue
        for g in groups:
            if type(g) is str:
                gname = g.strip()
                if gname in group_ratio:
                    group_models.setdefault(gname, set()).add(mname)

    if not group_models:
        return None

    # 1. Exact model-set match among public groups listed in group_ratio
    exact = [(g, group_ratio[g]) for g, ms in group_models.items() if ms == model_set]
    if exact:
        distinct_ratios = {round(r, 6) for _, r in exact}
        if len(distinct_ratios) == 1:
            exact.sort(key=lambda pair: (len(pair[0]), pair[0].lower()))
            chosen_group, chosen_ratio = exact[0]
            return {
                "group": chosen_group,
                "ratio": chosen_ratio,
                "confidence": "exact",
                "model_count": len(model_set),
            }
        return None

    # 2. Tight smallest superset match (key sees a subset of a single unambiguous group)
    supersets = [
        (len(ms), g, group_ratio[g])
        for g, ms in group_models.items()
        if model_set.issubset(ms) and len(ms) <= len(model_set) * 2 + 5
    ]
    if supersets:
        min_size = min(size for size, _, _ in supersets)
        smallest = [(g, r) for size, g, r in supersets if size == min_size]
        distinct_ratios = {round(r, 6) for _, r in smallest}
        if len(distinct_ratios) == 1:
            smallest.sort(key=lambda pair: (len(pair[0]), pair[0].lower()))
            chosen_group, chosen_ratio = smallest[0]
            return {
                "group": chosen_group,
                "ratio": chosen_ratio,
                "confidence": "subset",
                "model_count": len(model_set),
            }
    return None


def _adapt_newapi_classic_item(
    item: dict[str, Any],
    *,
    currency: str,
    unit_scale: float,
    ratio_per_million: float,
    require_positive: bool = False,
) -> dict[str, Any] | None:
    if item.get("quota_type") == 1:
        model_price = _nonneg_float(item.get("model_price"), scale=unit_scale)
        if model_price is None or (require_positive and model_price <= 0.0):
            return None
        return {
            "mode": "per_turn",
            "currency": currency,
            "price": model_price,
            "multiplier": 1.0,
        }

    model_ratio = _nonneg_float(item.get("model_ratio"), scale=1.0)
    if model_ratio is None or (require_positive and model_ratio <= 0.0):
        return None
    prompt_price = round(model_ratio * ratio_per_million * unit_scale, 6)
    if not math.isfinite(prompt_price) or prompt_price > MAX_PRICE_PER_MILLION:
        return None
    comp_ratio = _nonneg_float(item.get("completion_ratio"), scale=1.0)
    if comp_ratio is None:
        comp_ratio = 1.0
    completion_price = round(prompt_price * comp_ratio, 6)
    if not math.isfinite(completion_price) or completion_price > MAX_PRICE_PER_MILLION:
        return None

    cache_ratio = _nonneg_float(item.get("cache_ratio"), scale=1.0)
    cached_price = round(prompt_price * cache_ratio, 6) if cache_ratio is not None else None
    if cached_price is not None and (not math.isfinite(cached_price) or cached_price > MAX_PRICE_PER_MILLION):
        cached_price = None

    create_ratio = _nonneg_float(item.get("create_cache_ratio"), scale=1.0)
    if create_ratio is not None:
        creation_price = round(prompt_price * create_ratio, 6)
    else:
        creation_price = round(prompt_price * 1.25, 6)
    if not math.isfinite(creation_price) or creation_price > MAX_PRICE_PER_MILLION:
        creation_price = None

    return _make_per_token_rule(
        prompt_price,
        cached_price,
        creation_price,
        completion_price,
        currency=currency,
    )


def adapt_newapi(
    payload: object,
    *,
    source_id: str,
    source_label: str = "",
    updated_at: str | None = None,
    currency: str = "USD",
    unit_scale: float = 1.0,
    quota_per_unit: float = DEFAULT_NEWAPI_QUOTA_PER_UNIT,
    diagnostics: dict[str, int] | None = None,
) -> list[CatalogPrice]:
    """Adapt New API ``/api/pricing`` JSON with currency awareness and ``billing_expr`` support.

    Rules:
    - ``billing_mode == "tiered_expr"`` -> reads ``billing_expr`` first (losslessly lowering
      simple ``tier(...)`` token/context expressions into ``per_token`` / ``per_tier`` while
      keeping complex expressions in ``tiered_expr``), then falls back to legacy
      ``quota_conversion`` / ``quotaConversion`` / ``tiered_expr`` / ``expr`` / ``tiers``,
      and gracefully degrades to positive classic ``model_ratio`` / ``model_price`` when
      an expression uses unsupported gateway-only functions (never falsely marking 0 as free).
    - ``quota_type == 1`` -> ``per_turn`` (``model_price * unit_scale`` in ``currency``).
    - ``quota_type == 0`` -> ``per_token`` where ``prompt = model_ratio * (1e6 / quota_per_unit) * unit_scale``,
      ``completion = prompt * completion_ratio``, ``cache_read = prompt * cache_ratio``,
      ``cache_creation = prompt * create_cache_ratio`` (default ``1.25x``).
    - Keeps base prices unscaled by ``group_ratio`` so cluster multipliers stay orthogonal.
    """
    if type(payload) is not dict:
        return []
    if payload.get("success") is not True:
        return []
    items = payload.get("data")
    if type(items) is not list:
        return []

    norm_currency = "CNY" if currency == "CNY" else "USD"
    scale = float(unit_scale) if type(unit_scale) in (int, float) and math.isfinite(float(unit_scale)) and float(unit_scale) > 0 else 1.0
    qpu = float(quota_per_unit) if type(quota_per_unit) in (int, float) and math.isfinite(float(quota_per_unit)) and float(quota_per_unit) > 0 else DEFAULT_NEWAPI_QUOTA_PER_UNIT
    ratio_per_million = 1_000_000.0 / qpu

    diag = diagnostics if isinstance(diagnostics, dict) else None
    if diag is not None:
        for key in (
            "lowered_per_token",
            "lowered_per_tier",
            "kept_tiered_expr",
            "fallback_classic",
            "skipped_unsupported_expr",
        ):
            diag.setdefault(key, 0)

    label = source_label.strip() or source_id
    results: list[CatalogPrice] = []
    seen_keys: set[str] = set()

    for item in items:
        if type(item) is not dict:
            continue
        model_name = str(item.get("model_name") or "").strip()
        if not model_name:
            continue

        billing_mode = str(item.get("billing_mode") or "").strip().lower()
        raw_billing_expr = item.get("billing_expr")
        has_billing_expr = type(raw_billing_expr) is str and bool(raw_billing_expr.strip())
        rule: dict[str, Any] | None = None

        if billing_mode == "tiered_expr" or has_billing_expr:
            if has_billing_expr:
                try:
                    lowered_rule, lowering_kind = lower_newapi_billing_expr(
                        raw_billing_expr,
                        currency=norm_currency,
                        unit_scale=scale,
                        max_price=MAX_PRICE_PER_MILLION,
                    )
                    rule = lowered_rule
                    if diag is not None:
                        if lowering_kind == "per_token":
                            diag["lowered_per_token"] += 1
                        elif lowering_kind == "per_tier":
                            diag["lowered_per_tier"] += 1
                        else:
                            diag["kept_tiered_expr"] += 1
                except PricingExprError:
                    fallback = _adapt_newapi_classic_item(
                        item,
                        currency=norm_currency,
                        unit_scale=scale,
                        ratio_per_million=ratio_per_million,
                        require_positive=True,
                    )
                    if fallback is not None:
                        rule = fallback
                        if diag is not None:
                            diag["fallback_classic"] += 1
                    else:
                        if diag is not None:
                            diag["skipped_unsupported_expr"] += 1
                        continue
            else:
                raw_spec = (
                    item.get("quota_conversion")
                    if item.get("quota_conversion") is not None
                    else item.get("quotaConversion")
                    if item.get("quotaConversion") is not None
                    else item.get("tiered_expr")
                    if item.get("tiered_expr") is not None
                    else item.get("expr")
                    if item.get("expr") is not None
                    else item.get("tiers")
                )
                if type(raw_spec) is list:
                    raw_spec = {"tiers": raw_spec}
                if raw_spec is not None:
                    if type(raw_spec) is str:
                        spec_for_val: Any = {
                            "expr": (
                                f"({raw_spec.strip()[3:].strip() if raw_spec.strip().startswith('v1:') else raw_spec.strip()}) * {round(scale, 6):g}"
                                if abs(scale - 1.0) > 1e-12
                                else raw_spec
                            ),
                            "currency": norm_currency,
                        }
                    elif type(raw_spec) is dict:
                        explicit_cur = raw_spec.get("currency")
                        has_explicit_cur = (
                            type(explicit_cur) is str and explicit_cur.strip().upper() in ("USD", "CNY")
                        )
                        eff_cur = explicit_cur.strip().upper() if has_explicit_cur else norm_currency
                        spec_for_val = {**raw_spec, "currency": eff_cur}
                        if abs(scale - 1.0) > 1e-12 and not has_explicit_cur:
                            raw_cm = spec_for_val.get("cluster_multiplier", 1.0)
                            if raw_cm is None:
                                cm_val: float | None = 1.0
                            elif (
                                type(raw_cm) in (int, float)
                                and not isinstance(raw_cm, bool)
                                and math.isfinite(float(raw_cm))
                                and float(raw_cm) >= 0.0
                            ):
                                cm_val = float(raw_cm)
                            else:
                                cm_val = None
                            if cm_val is None:
                                spec_for_val = None
                            else:
                                spec_for_val["cluster_multiplier"] = round(cm_val * scale, 6)
                    else:
                        spec_for_val = raw_spec
                    if spec_for_val is not None:
                        validation = validate_tiered_expr_rule(spec_for_val)
                        if validation["valid"]:
                            rule = validation["normalized"]
                            if diag is not None:
                                diag["kept_tiered_expr"] += 1
                if rule is None:
                    fallback = _adapt_newapi_classic_item(
                        item,
                        currency=norm_currency,
                        unit_scale=scale,
                        ratio_per_million=ratio_per_million,
                        require_positive=True,
                    )
                    if fallback is not None:
                        rule = fallback
                        if diag is not None:
                            diag["fallback_classic"] += 1
                    else:
                        if diag is not None:
                            diag["skipped_unsupported_expr"] += 1
                        continue
        else:
            rule = _adapt_newapi_classic_item(
                item,
                currency=norm_currency,
                unit_scale=scale,
                ratio_per_million=ratio_per_million,
                require_positive=False,
            )
            if rule is None:
                continue

        price_key = f"{source_id}:{model_name}"
        if price_key in seen_keys:
            continue
        seen_keys.add(price_key)
        vendor = model_name.split("/", 1)[0] if "/" in model_name else label
        results.append(CatalogPrice(
            price_key=price_key,
            source_id=source_id,
            source_label=label,
            model_id=model_name,
            vendor=vendor,
            display_name=model_name,
            rule=rule,
            updated_at=updated_at,
        ))
    return results


def _variant_tokens(slug: str) -> frozenset[str]:
    if not slug:
        return frozenset()
    parts = slug.split("-")
    return frozenset(part for part in parts if part in _VARIANT_TOKENS)


@lru_cache(maxsize=32768)
def _match_features(
    model_name: str,
) -> tuple[str, str, str, str, frozenset[str], frozenset[str], str]:
    raw = str(model_name or "").strip().lower()
    if not raw:
        return ("", "", "", "", frozenset(), frozenset(), "")
    novendor = _strip_vendor_prefix(raw)
    raw_slug = _slug_key(novendor)
    norm = _normalize_model_name(raw)
    slug = _slug_key(norm)
    if not slug:
        return (raw, novendor, raw_slug, "", frozenset(), frozenset(), "")
    variants = _variant_tokens(slug)
    parts = frozenset(slug.split("-"))
    return (raw, novendor, raw_slug, slug, variants, parts, slug + "-")


def _score_features(
    qf: tuple[str, str, str, str, frozenset[str], frozenset[str], str],
    cf: tuple[str, str, str, str, frozenset[str], frozenset[str], str],
) -> float:
    q_raw, q_novendor, q_raw_slug, q_slug, q_variants, q_parts, q_dash = qf
    c_raw, c_novendor, c_raw_slug, c_slug, c_variants, c_parts, c_dash = cf
    if not q_raw or not c_raw:
        return 0.0
    if q_raw == c_raw:
        return 1.0
    if q_novendor and q_novendor == c_novendor:
        return 0.98
    if q_raw_slug and q_raw_slug == c_raw_slug:
        return 0.96
    if not q_slug or not c_slug:
        return 0.0

    variants_match = q_variants == c_variants
    if q_slug == c_slug:
        return 0.92 if variants_match else 0.60

    # Clean boundary prefix match:
    # - Query more specific than candidate (e.g. gpt-4o-custom -> gpt-4o): eligible if variants match.
    # - Candidate has extra non-date suffix tokens (e.g. gemini-3.6-flash -> gemini-3.6-flash-as / -once):
    #   NEVER eligible for auto-match (capped <= 0.78), kept only for manual comparison!
    if q_slug.startswith(c_dash):
        ratio = len(c_slug) / len(q_slug) if q_slug else 0.0
        if variants_match and ratio >= 0.72:
            return round(0.85 + 0.05 * ratio, 4)
        return round(min(0.65, 0.50 + 0.15 * ratio), 4)
    if c_slug.startswith(q_dash):
        ratio = len(q_slug) / len(c_slug) if c_slug else 0.0
        if variants_match:
            return round(min(0.78, 0.60 + 0.18 * ratio), 4)
        return round(min(0.62, 0.45 + 0.15 * ratio), 4)

    # Token overlap for manual candidate discovery (always < AUTO_MATCH_THRESHOLD)
    if q_parts.isdisjoint(c_parts):
        return 0.0
    overlap = len(q_parts & c_parts)
    union = len(q_parts) + len(c_parts) - overlap
    jaccard = overlap / union if union else 0.0
    if not variants_match:
        jaccard *= 0.65
    if jaccard < 0.35:
        return 0.0
    return round(min(0.79, jaccard * 0.80), 4)


def score_candidate_match(query_model: str, candidate_model: str) -> float:
    """Compute a deterministic similarity score in ``[0.0, 1.0]`` for catalog matching."""
    return _score_features(
        _match_features(str(query_model or "")),
        _match_features(str(candidate_model or "")),
    )


def _source_sort_rank(source_id: str, is_own_gateway: bool) -> int:
    if is_own_gateway:
        return 0
    if source_id.startswith("newapi:"):
        return 1
    return _SOURCE_PRIORITY.get(source_id, 10) + 1


def find_candidates(
    model_name: str,
    *,
    catalog: dict[str, Any] | None,
    provider_id: str = "",
    own_source_ids: frozenset[str] = frozenset(),
    enabled_sources: set[str] | frozenset[str] | None = None,
    limit: int = MAX_CANDIDATES_PER_MODEL,
) -> list[Candidate]:
    """Find and rank matching catalog candidates for a model name."""
    query = str(model_name or "").strip()
    if not query or not isinstance(catalog, dict):
        return []
    prices = catalog.get("prices")
    if not isinstance(prices, dict) or not prices:
        return []

    own_ids = set(own_source_ids)
    if provider_id:
        own_ids.add(f"newapi:{provider_id}")

    q_feat = _match_features(query)
    if not q_feat[0]:
        return []

    candidates: list[Candidate] = []
    for price_key, raw in prices.items():
        if type(raw) is not dict:
            continue
        source_id = str(raw.get("source_id") or "")
        if not source_id:
            continue
        if enabled_sources is not None and source_id not in enabled_sources:
            continue
        cand_model = str(raw.get("model_id") or "")
        score = _score_features(q_feat, _match_features(cand_model))
        if score <= 0.0:
            continue
        is_own = source_id in own_ids
        candidates.append(Candidate(
            price_key=str(raw.get("price_key") or price_key),
            source_id=source_id,
            source_label=str(raw.get("source_label") or source_id),
            model_id=cand_model,
            vendor=str(raw.get("vendor") or ""),
            display_name=str(raw.get("display_name") or cand_model),
            score=score,
            is_own_gateway=is_own,
            rule=deepcopy(raw.get("rule") or {}),
        ))

    candidates.sort(
        key=lambda c: (
            not (c.is_own_gateway and c.score >= AUTO_MATCH_THRESHOLD),
            -c.score,
            not c.is_own_gateway,
            _source_sort_rank(c.source_id, c.is_own_gateway),
            c.model_id.lower(),
            c.price_key.lower(),
        )
    )
    return candidates[: max(1, int(limit))]


def select_auto_candidate(
    candidates: list[Candidate],
    *,
    threshold: float = AUTO_MATCH_THRESHOLD,
) -> Candidate | None:
    """Select an automatic catalog match only when confidence >= threshold and unambiguous.

    Per-call rules (``per_turn`` / ``per_request``) require a near-exact match
    (``>= PER_CALL_AUTO_MATCH_THRESHOLD``) so token-billed models never auto-match
    onto per-call SKUs, and a weak own-gateway fuzzy match cannot override an exact match.
    """
    eligible = [
        c
        for c in candidates
        if c.score >= (
            max(threshold, PER_CALL_AUTO_MATCH_THRESHOLD)
            if str((c.rule or {}).get("mode") or "") in ("per_turn", "per_request")
            else threshold
        )
    ]
    if not eligible:
        return None

    max_all = max(c.score for c in eligible)
    own = [c for c in eligible if c.is_own_gateway]
    max_own = max((c.score for c in own), default=0.0)
    pool = own if (own and (max_own >= 0.92 or max_own >= max_all - 0.05)) else eligible
    top_score = max(c.score for c in pool)
    top_group = [c for c in pool if abs(c.score - top_score) < 1e-9]
    distinct_models = {_slug_key(_normalize_model_name(c.model_id)) for c in top_group}
    if len(distinct_models) != 1:
        return None
    top_group.sort(key=lambda c: (_source_sort_rank(c.source_id, c.is_own_gateway), c.price_key.lower()))
    return top_group[0]


def empty_catalog() -> dict[str, Any]:
    sources: dict[str, dict[str, Any]] = {}
    for spec in PUBLIC_SOURCE_SPECS:
        sources[spec["source_id"]] = SourceStatus(
            source_id=spec["source_id"],
            kind=spec["kind"],
            label=spec["label"],
            url=spec["url"],
            enabled=True,
            status="pending",
            model_count=0,
        ).to_dict()
    return {
        "version": 2,
        "updated_at": None,
        "sources": sources,
        "prices": {},
    }


_CATALOG_CACHE_MAX_ENTRIES = 16
_CATALOG_CACHE: OrderedDict[Path, tuple[tuple[int, int, int], dict[str, Any]]] = OrderedDict()


class PriceCatalogStore:
    """Atomic, stat-cached JSON store for synced price catalog at ``<runtime>/pricing/catalog.json``."""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).resolve()
        self.directory = self.root / "pricing"
        self.path = self.directory / "catalog.json"
        self._cache_stat: tuple[int, int, int] | None = None
        self._cache_value: dict[str, Any] | None = None

    def _read_uncached(self) -> dict[str, Any]:
        if not self.path.exists():
            return empty_catalog()
        raw = self.path.read_bytes()
        if len(raw) > MAX_CATALOG_BYTES:
            raise ValueError("price catalog file exceeded maximum size")
        data = json.loads(raw.decode("utf-8"))
        if type(data) is not dict:
            raise ValueError("price catalog root must be an object")
        base = empty_catalog()
        if isinstance(data.get("sources"), dict):
            for sid, status in data["sources"].items():
                if type(sid) is str and type(status) is dict:
                    base["sources"][sid] = deepcopy(status)
        if isinstance(data.get("prices"), dict):
            base["prices"] = deepcopy(data["prices"])
        base["updated_at"] = data.get("updated_at") if type(data.get("updated_at")) is str else None
        return base

    def read_safe(self, *, copy: bool = True) -> dict[str, Any]:
        with _CATALOG_LOCK:
            try:
                st = self.path.stat()
            except OSError:
                self._cache_stat = None
                self._cache_value = None
                _CATALOG_CACHE.pop(self.path, None)
                return empty_catalog()
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
            if self._cache_value is not None and self._cache_stat == sig:
                return deepcopy(self._cache_value) if copy else self._cache_value
            shared = _CATALOG_CACHE.get(self.path)
            if shared is not None and shared[0] == sig:
                _CATALOG_CACHE.move_to_end(self.path)
                self._cache_stat = sig
                self._cache_value = shared[1]
                return deepcopy(shared[1]) if copy else shared[1]
            try:
                value = self._read_uncached()
            except (OSError, UnicodeError, ValueError):
                return empty_catalog()
            self._cache_stat = sig
            self._cache_value = value
            _CATALOG_CACHE[self.path] = (sig, value)
            _CATALOG_CACHE.move_to_end(self.path)
            while len(_CATALOG_CACHE) > _CATALOG_CACHE_MAX_ENTRIES:
                _CATALOG_CACHE.popitem(last=False)
            return deepcopy(value) if copy else value

    def get_price(self, price_key: str) -> CatalogPrice | None:
        if type(price_key) is not str or not price_key:
            return None
        raw = self.read_safe(copy=False).get("prices", {}).get(price_key)
        return CatalogPrice.from_dict(raw) if isinstance(raw, dict) else None

    def save_source_result(
        self,
        status: SourceStatus,
        prices: list[CatalogPrice] | None,
        *,
        updated_at: str | None = None,
        preserve_existing_prices: bool = False,
    ) -> dict[str, Any]:
        """Atomically update one source's status and prices without clobbering other sources."""
        return self.save_batch_results(
            [(status, prices, preserve_existing_prices)],
            updated_at=updated_at,
        )

    def save_batch_results(
        self,
        updates: list[tuple[SourceStatus, list[CatalogPrice] | None, bool]],
        *,
        updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply one or more source sync results in a single write."""
        with _CATALOG_LOCK:
            current = self.read_safe()
            replace_source_ids: set[str] = set()
            new_prices: list[CatalogPrice] = []
            for status, prices, preserve_existing in updates:
                current["sources"][status.source_id] = status.to_dict()
                if not preserve_existing and prices is not None:
                    replace_source_ids.add(status.source_id)
                    new_prices.extend(prices)
            if replace_source_ids:
                filtered = {
                    key: val
                    for key, val in current["prices"].items()
                    if not (isinstance(val, dict) and val.get("source_id") in replace_source_ids)
                }
                for item in new_prices:
                    filtered[item.price_key] = item.to_dict()
                current["prices"] = filtered
            if updated_at:
                current["updated_at"] = updated_at
            self._write_atomic(current)
            self._cache_stat = None
            self._cache_value = None
            _CATALOG_CACHE.pop(self.path, None)
            return deepcopy(current)

    def remove_source(self, source_id: str) -> dict[str, Any]:
        with _CATALOG_LOCK:
            current = self.read_safe()
            current["sources"].pop(source_id, None)
            current["prices"] = {
                key: val
                for key, val in current["prices"].items()
                if not (isinstance(val, dict) and val.get("source_id") == source_id)
            }
            self._write_atomic(current)
            self._cache_stat = None
            self._cache_value = None
            _CATALOG_CACHE.pop(self.path, None)
            return deepcopy(current)

    def _write_atomic(self, payload_obj: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(payload_obj, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(self.directory), prefix=".catalog.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.path)
        finally:
            tmp_path.unlink(missing_ok=True)
