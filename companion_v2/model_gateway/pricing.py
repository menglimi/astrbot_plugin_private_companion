"""Pure domain Pricing v2 engine: 5 pricing modes, 6-tier resolution, schedules,
cluster multipliers, currency conversion, and atomic settings store.

Strictly pure Python stdlib — zero network I/O and zero host/framework imports.
All outbound HTTP syncs live in ``astrbot_host/exchange_rate.py`` and
``astrbot_host/pricing_sources.py``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any
from uuid import uuid4

from .models import Amount
from .pricing_catalog import (
    DEFAULT_MODEL_TARIFFS,
    PUBLIC_SOURCE_IDS,
    PUBLIC_SOURCE_SPECS,
    PresetTariff,
    PriceCatalogStore,
    _normalize_model_name,
    _slug_key,
    catalog_presets,
    find_candidates,
    match_builtin_tariff,
    select_auto_candidate,
)
from .pricing_expr import (
    PricingExprError,
    TieredExprEvaluationError,
    evaluate_tiered_expr_rule,
    normalize_tiered_expr_rule,
    validate_tiered_expr_rule,
)
from .pricing_schedule import (
    DEFAULT_PRICING_TIMEZONE,
    PricingScheduleError,
    match_schedule_period,
    normalize_schedule,
    schedule_is_active,
    validate_timezone,
)


SUPPORTED_CURRENCIES = ("CNY", "USD")
RATE_MODES = ("auto", "manual")
PRICING_MODES = ("per_token", "per_tier", "tiered_expr", "per_turn", "per_request")
DEFAULT_DISPLAY_CURRENCY = "CNY"
DEFAULT_FALLBACK_USD_CNY = 7.2
MIN_USD_CNY = 1.0
MAX_USD_CNY = 100.0
MIN_MULTIPLIER = 0.0001
MIN_V2_MULTIPLIER = 0.0
MAX_MULTIPLIER = 100.0
MAX_PRICE_PER_MILLION = 1_000_000.0
MAX_MICROUSD_RATE = 10**12
MAX_SETTINGS_BYTES = 1_048_576  # 1 MB for v2 schedules/rules
MAX_PROVIDER_TARIFFS = 128
MAX_PRICING_RULES = 512
MAX_PRICING_MULTIPLIERS = 256
MAX_PRICING_SCHEDULES = 256
MAX_PRICE_SELECTIONS = 512
MAX_PROVIDER_ID_CHARS = 128
MAX_RULE_KEY_CHARS = 260
MAX_TIERS_PER_RULE = 32
EXCHANGE_RATE_TTL_SECONDS = 12 * 3600
CATALOG_DAILY_SYNC_SECONDS = 24 * 3600

_RATE_SOURCE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,63}\Z")
_SOURCE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}\Z")
_SETTINGS_LOCK = threading.RLock()


class PricingSettingsError(ValueError):
    def __init__(self, code: str, reason: str):
        super().__init__(reason)
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class UsageTariff:
    """Explicit rated cost in microUSD per million tokens; not a billing receipt."""

    input: int
    cached_input: int
    output: int
    cache_creation: int | None = None
    configured: bool = True

    def __post_init__(self):
        if any(
            type(v) is not int or not 0 <= v <= MAX_MICROUSD_RATE
            for v in (self.input, self.cached_input, self.output)
        ):
            raise ValueError("tariff requires nonnegative integer microUSD rates")
        if self.cache_creation is not None and (
            type(self.cache_creation) is not int or not 0 <= self.cache_creation <= MAX_MICROUSD_RATE
        ):
            raise ValueError("tariff cache_creation requires None or nonnegative integer microUSD rate")
        if type(self.configured) is not bool:
            raise ValueError("tariff configured flag must be a boolean")

    @classmethod
    def unconfigured(cls) -> "UsageTariff":
        return cls(0, 0, 0, None, configured=False)

    @property
    def effective_cache_creation(self) -> int:
        return self.input if self.cache_creation is None else self.cache_creation

    def document(self) -> dict[str, Any]:
        """Preserve the canonical v1 profile document shape unless cache_creation is set."""
        data = {"input": self.input, "cached_input": self.cached_input, "output": self.output}
        if self.cache_creation is not None:
            data["cache_creation"] = self.cache_creation
        return data

    def reserve_amount(self, input_tokens: int, output_tokens: int) -> Amount:
        """Return a concrete integer-cost reservation amount for budget admission."""
        total = input_tokens + output_tokens
        if not self.configured:
            return Amount(total, 0)
        rate = max(self.input, self.cached_input, self.effective_cache_creation)
        cost = (input_tokens * rate + output_tokens * self.output + 999999) // 1000000
        return Amount(total, min(cost, MAX_MICROUSD_RATE))

    def amount(
        self,
        other: int,
        cached: int,
        output: int,
        *,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        cache_creation_tokens: int | None = None,
    ) -> Amount:
        if not self.configured:
            return Amount(
                other + cached + output,
                None,
                input_tokens=input_tokens,
                cached_input_tokens=cached_input_tokens,
                cache_creation_tokens=cache_creation_tokens,
                output_tokens=output if input_tokens is not None else None,
            )
        creation = (
            cache_creation_tokens
            if type(cache_creation_tokens) is int and cache_creation_tokens > 0
            else 0
        )
        billable_creation = min(other, creation)
        uncached_input = other - billable_creation
        creation_rate = self.effective_cache_creation
        cost = (
            uncached_input * self.input
            + cached * self.cached_input
            + billable_creation * creation_rate
            + output * self.output
            + 999999
        ) // 1000000
        return Amount(
            other + cached + output,
            cost,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_creation_tokens=cache_creation_tokens,
            output_tokens=output if input_tokens is not None else None,
        )


@dataclass(frozen=True)
class ResolvedTariff:
    tariff: UsageTariff
    source: str
    preset_id: str | None = None
    currency: str = "USD"
    multiplier: float = 1.0


@dataclass(frozen=True)
class EffectivePricingRule:
    rule: dict[str, Any]
    source: str
    preset_id: str | None = None
    price_key: str | None = None
    source_id: str | None = None
    source_label: str | None = None
    schedule_period_id: str | None = None
    schedule_period_name: str | None = None
    cluster_multiplier: float = 1.0
    currency: str = "USD"
    matched_key: str | None = None

    @property
    def mode(self) -> str:
        return str(self.rule.get("mode") or "per_token")

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "source": self.source,
            "preset_id": self.preset_id,
            "price_key": self.price_key,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "schedule_period_id": self.schedule_period_id,
            "schedule_period_name": self.schedule_period_name,
            "cluster_multiplier": self.cluster_multiplier,
            "currency": self.currency,
            "rule": deepcopy(self.rule),
        }


def default_price_sources() -> dict[str, dict[str, Any]]:
    return {
        spec["source_id"]: {
            "enabled": True,
            "kind": spec["kind"],
            "label": spec["label"],
            "url": spec["url"],
        }
        for spec in PUBLIC_SOURCE_SPECS
    }


def default_pricing_settings() -> dict[str, Any]:
    return {
        "version": 2,
        "display_currency": DEFAULT_DISPLAY_CURRENCY,
        "use_builtin_presets": True,
        "exchange_rate": {
            "mode": "auto",
            "manual_usd_cny": None,
            "cached_usd_cny": DEFAULT_FALLBACK_USD_CNY,
            "fallback_usd_cny": DEFAULT_FALLBACK_USD_CNY,
            "fetched_at": None,
            "source": "static_fallback",
        },
        "provider_tariffs": {},
        "pricing_rules": {},
        "pricing_multipliers": {},
        "pricing_schedules": {},
        "pricing_timezone": DEFAULT_PRICING_TIMEZONE,
        "price_sources": default_price_sources(),
        "price_selections": {},
        "price_sync": {
            "auto_daily": True,
            "last_auto_sync_at": None,
        },
        "revision": "initial",
    }


def _finite_number(
    value: object,
    *,
    minimum: float,
    maximum: float,
    code: str,
    reason: str,
) -> float:
    if isinstance(value, bool) or type(value) not in (int, float):
        raise PricingSettingsError(code, reason)
    numeric = float(value)
    if not math.isfinite(numeric) or not (minimum <= numeric <= maximum):
        raise PricingSettingsError(code, reason)
    return numeric


def _optional_price(value: object, *, field_label: str) -> float | None:
    if value is None or value == "":
        return None
    return round(
        _finite_number(
            value,
            minimum=0.0,
            maximum=MAX_PRICE_PER_MILLION,
            code="pricing_config_invalid",
            reason=f"{field_label} 必须是 0 到 {int(MAX_PRICE_PER_MILLION)} 之间的数字",
        ),
        6,
    )


def _optional_multiplier(value: object, *, field_label: str) -> float | None:
    if value is None or value == "":
        return None
    return round(
        _finite_number(
            value,
            minimum=MIN_V2_MULTIPLIER,
            maximum=MAX_MULTIPLIER,
            code="pricing_config_invalid",
            reason=f"{field_label} 必须是 0 到 {int(MAX_MULTIPLIER)} 之间的数字",
        ),
        6,
    )


def _validate_iso_timestamp(value: object, *, code: str, label: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > 64:
        raise PricingSettingsError(code, f"{label}格式无效")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise PricingSettingsError(code, f"{label}格式无效") from error
    return value


def _validate_rule_key(key: object, *, label: str = "计价规则标识") -> str:
    if type(key) is not str:
        raise PricingSettingsError("pricing_config_invalid", f"{label}必须是字符串")
    clean = key.strip()
    if not clean or len(clean) > MAX_RULE_KEY_CHARS or clean != key:
        raise PricingSettingsError("pricing_config_invalid", f"{label}无效")
    return clean


def _validate_provider_tariff(provider_id: object, raw: object) -> tuple[str, dict[str, Any]]:
    """Validate v1 ``provider_tariffs`` rule strictly (backward compatibility)."""
    if type(provider_id) is not str:
        raise PricingSettingsError("pricing_config_invalid", "Provider 标识必须是字符串")
    clean_id = provider_id.strip()
    if not clean_id or len(clean_id) > MAX_PROVIDER_ID_CHARS or clean_id != provider_id:
        raise PricingSettingsError("pricing_config_invalid", "Provider 标识无效")
    if type(raw) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "Provider 单价规则必须是对象")
    allowed = {
        "currency", "input_per_million", "cached_input_per_million",
        "cache_creation_per_million", "output_per_million", "multiplier", "free",
    }
    if set(raw) - allowed:
        raise PricingSettingsError("pricing_config_invalid", "Provider 单价包含不支持的字段")
    currency = raw.get("currency", "USD")
    if currency not in SUPPORTED_CURRENCIES:
        raise PricingSettingsError("pricing_config_invalid", "单价币种仅支持 USD 或 CNY")
    free = raw.get("free", False)
    if type(free) is not bool:
        raise PricingSettingsError("pricing_config_invalid", "免费开关必须是布尔值")
    multiplier = round(
        _finite_number(
            raw.get("multiplier", 1.0),
            minimum=MIN_MULTIPLIER,
            maximum=MAX_MULTIPLIER,
            code="pricing_config_invalid",
            reason=f"倍率必须在 {MIN_MULTIPLIER} 到 {int(MAX_MULTIPLIER)} 之间",
        ),
        6,
    )
    if free:
        return clean_id, {
            "currency": currency,
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "cache_creation_per_million": 0.0,
            "output_per_million": 0.0,
            "multiplier": multiplier,
            "free": True,
        }
    inp = _optional_price(raw.get("input_per_million"), field_label="输入单价")
    out = _optional_price(raw.get("output_per_million"), field_label="输出单价")
    cached = _optional_price(raw.get("cached_input_per_million"), field_label="缓存读单价")
    creation = _optional_price(raw.get("cache_creation_per_million"), field_label="缓存写单价")
    if inp is None or out is None:
        raise PricingSettingsError("pricing_config_invalid", "非免费模型必须填写输入单价与输出单价")
    return clean_id, {
        "currency": currency,
        "input_per_million": inp,
        "cached_input_per_million": cached,
        "cache_creation_per_million": creation,
        "output_per_million": out,
        "multiplier": multiplier,
        "free": False,
    }


def _v1_tariff_to_v2_rule(v1_rule: dict[str, Any]) -> dict[str, Any]:
    """Migrate a v1 ``provider_tariffs`` entry into a v2 ``per_token`` rule."""
    currency = v1_rule.get("currency", "USD")
    mult = float(v1_rule.get("multiplier", 1.0))
    free = bool(v1_rule.get("free", False))
    if free:
        return {
            "mode": "per_token",
            "currency": currency,
            "input": 0.0,
            "input_cached": 0.0,
            "cache_creation": 0.0,
            "output": 0.0,
            "multiplier": round(mult, 6),
            "free": True,
            "configured": {
                "input": True,
                "input_cached": True,
                "cache_creation": True,
                "output": True,
            },
        }
    inp = v1_rule.get("input_per_million")
    cached = v1_rule.get("cached_input_per_million")
    creation = v1_rule.get("cache_creation_per_million")
    out = v1_rule.get("output_per_million")
    return {
        "mode": "per_token",
        "currency": currency,
        "input": inp,
        "input_cached": cached,
        "cache_creation": creation,
        "output": out,
        "multiplier": round(mult, 6),
        "free": False,
        "configured": {
            "input": inp is not None,
            "input_cached": cached is not None,
            "cache_creation": creation is not None,
            "output": out is not None,
        },
    }


def _v2_rule_to_v1_mirror(v2_rule: dict[str, Any]) -> dict[str, Any] | None:
    """Best-effort mirror of a provider-level ``per_token`` rule for v1 readers."""
    if v2_rule.get("mode") != "per_token":
        return None
    mult = float(v2_rule.get("multiplier", 1.0))
    free = bool(v2_rule.get("free", False)) or mult == 0.0
    if free:
        return {
            "currency": v2_rule.get("currency", "USD"),
            "input_per_million": 0.0,
            "cached_input_per_million": 0.0,
            "cache_creation_per_million": 0.0,
            "output_per_million": 0.0,
            "multiplier": max(MIN_MULTIPLIER, mult) if mult > 0 else 1.0,
            "free": True,
        }
    inp = v2_rule.get("input")
    out = v2_rule.get("output")
    if inp is None or out is None:
        return None
    return {
        "currency": v2_rule.get("currency", "USD"),
        "input_per_million": inp,
        "cached_input_per_million": v2_rule.get("input_cached"),
        "cache_creation_per_million": v2_rule.get("cache_creation"),
        "output_per_million": out,
        "multiplier": round(max(MIN_MULTIPLIER, mult), 6),
        "free": False,
    }


def validate_pricing_rule(raw: object) -> dict[str, Any]:
    """Validate and normalize a 5-mode Pricing v2 rule."""
    if type(raw) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "计价规则必须是对象")
    mode = raw.get("mode", "per_token")
    if mode not in PRICING_MODES:
        raise PricingSettingsError("pricing_config_invalid", f"不支持的计价模式: {mode}")

    if mode == "per_token":
        allowed = {
            "mode", "currency", "input", "input_cached", "cache_creation", "output",
            "input_per_million", "cached_input_per_million",
            "cache_creation_per_million", "output_per_million",
            "multiplier", "free", "configured",
        }
        if set(raw) - allowed:
            raise PricingSettingsError("pricing_config_invalid", "per_token 规则包含不支持的字段")
        currency = raw.get("currency", "USD")
        if currency not in SUPPORTED_CURRENCIES:
            raise PricingSettingsError("pricing_config_invalid", "单价币种仅支持 USD 或 CNY")
        free = raw.get("free", False)
        if type(free) is not bool:
            raise PricingSettingsError("pricing_config_invalid", "免费开关必须是布尔值")
        multiplier = round(
            _finite_number(
                raw.get("multiplier", 1.0),
                minimum=MIN_V2_MULTIPLIER,
                maximum=MAX_MULTIPLIER,
                code="pricing_config_invalid",
                reason="倍率必须在 0 到 100 之间",
            ),
            6,
        )
        if free:
            return {
                "mode": "per_token",
                "currency": currency,
                "input": 0.0,
                "input_cached": 0.0,
                "cache_creation": 0.0,
                "output": 0.0,
                "multiplier": multiplier,
                "free": True,
                "configured": {
                    "input": True,
                    "input_cached": True,
                    "cache_creation": True,
                    "output": True,
                },
            }
        raw_cfg = raw.get("configured")
        if raw_cfg is not None and type(raw_cfg) is not dict:
            raise PricingSettingsError("pricing_config_invalid", "configured 必须是布尔映射")
        raw_cfg = raw_cfg or {}

        field_specs = (
            ("input", "input_per_million", "输入单价"),
            ("input_cached", "cached_input_per_million", "缓存读单价"),
            ("cache_creation", "cache_creation_per_million", "缓存写单价"),
            ("output", "output_per_million", "输出单价"),
        )
        values: dict[str, float | None] = {}
        configured: dict[str, bool] = {}
        for field_name, alias, label in field_specs:
            explicit_flag = raw_cfg.get(field_name)
            if explicit_flag is not None and type(explicit_flag) is not bool:
                raise PricingSettingsError("pricing_config_invalid", f"configured.{field_name} 必须是布尔值")
            raw_val = raw.get(field_name) if field_name in raw else raw.get(alias)
            if explicit_flag is False:
                values[field_name] = None
                configured[field_name] = False
            else:
                parsed = _optional_price(raw_val, field_label=label)
                values[field_name] = parsed
                configured[field_name] = parsed is not None
        return {
            "mode": "per_token",
            "currency": currency,
            "input": values["input"],
            "input_cached": values["input_cached"],
            "cache_creation": values["cache_creation"],
            "output": values["output"],
            "multiplier": multiplier,
            "free": False,
            "configured": configured,
        }

    if mode == "per_tier":
        allowed = {"mode", "currency", "base", "context_tiers", "service_tiers", "multiplier"}
        if set(raw) - allowed:
            raise PricingSettingsError("pricing_config_invalid", "per_tier 规则包含不支持的字段")
        currency = raw.get("currency", "USD")
        if currency not in SUPPORTED_CURRENCIES:
            raise PricingSettingsError("pricing_config_invalid", "单价币种仅支持 USD 或 CNY")
        multiplier = round(
            _finite_number(
                raw.get("multiplier", 1.0),
                minimum=MIN_V2_MULTIPLIER,
                maximum=MAX_MULTIPLIER,
                code="pricing_config_invalid",
                reason="倍率必须在 0 到 100 之间",
            ),
            6,
        )
        base_raw = raw.get("base")
        if type(base_raw) is not dict:
            raise PricingSettingsError("pricing_config_invalid", "per_tier 规则必须包含 base 基础单价对象")
        base_allowed = {"input", "input_cached", "cache_creation", "output"}
        if set(base_raw) - base_allowed:
            raise PricingSettingsError("pricing_config_invalid", "per_tier.base 包含不支持的字段")
        base_in = _optional_price(base_raw.get("input"), field_label="基础输入单价")
        base_out = _optional_price(base_raw.get("output"), field_label="基础输出单价")
        base_cached = _optional_price(base_raw.get("input_cached"), field_label="基础缓存读单价")
        base_create = _optional_price(base_raw.get("cache_creation"), field_label="基础缓存写单价")
        if base_in is None or base_out is None:
            raise PricingSettingsError("pricing_config_invalid", "per_tier 基础单价必须填写输入与输出单价")

        raw_ctx = raw.get("context_tiers", [])
        if type(raw_ctx) is not list or len(raw_ctx) > MAX_TIERS_PER_RULE:
            raise PricingSettingsError("pricing_config_invalid", f"context_tiers 最多支持 {MAX_TIERS_PER_RULE} 档")
        ctx_tiers: list[dict[str, Any]] = []
        seen_thresholds: set[int] = set()
        ctx_allowed = {"threshold_tokens", "input", "input_cached", "cache_creation", "output"}
        for idx, tier in enumerate(raw_ctx):
            if type(tier) is not dict or (set(tier) - ctx_allowed):
                raise PricingSettingsError("pricing_config_invalid", f"第 {idx + 1} 个上下文阶梯格式无效")
            thresh = tier.get("threshold_tokens")
            if type(thresh) is not int or not (1 <= thresh <= 10**9):
                raise PricingSettingsError("pricing_config_invalid", f"第 {idx + 1} 个上下文阈值必须是正整数")
            if thresh in seen_thresholds:
                raise PricingSettingsError("pricing_config_invalid", f"上下文阶梯阈值重复: {thresh}")
            seen_thresholds.add(thresh)
            ctx_tiers.append({
                "threshold_tokens": thresh,
                "input": _optional_price(tier.get("input"), field_label="阶梯输入单价"),
                "input_cached": _optional_price(tier.get("input_cached"), field_label="阶梯缓存读单价"),
                "cache_creation": _optional_price(tier.get("cache_creation"), field_label="阶梯缓存写单价"),
                "output": _optional_price(tier.get("output"), field_label="阶梯输出单价"),
            })
        ctx_tiers.sort(key=lambda item: item["threshold_tokens"])

        raw_svc = raw.get("service_tiers", [])
        if type(raw_svc) is not list or len(raw_svc) > MAX_TIERS_PER_RULE:
            raise PricingSettingsError("pricing_config_invalid", f"service_tiers 最多支持 {MAX_TIERS_PER_RULE} 档")
        svc_tiers: list[dict[str, Any]] = []
        svc_allowed = {
            "match",
            "input_multiplier", "input_cached_multiplier",
            "cache_creation_multiplier", "output_multiplier",
            "input", "input_cached", "cache_creation", "output",
        }
        for idx, tier in enumerate(raw_svc):
            if type(tier) is not dict or (set(tier) - svc_allowed):
                raise PricingSettingsError("pricing_config_invalid", f"第 {idx + 1} 个服务档位格式无效")
            match_val = tier.get("match")
            if type(match_val) is not str or not match_val.strip() or len(match_val.strip()) > 64:
                raise PricingSettingsError("pricing_config_invalid", f"第 {idx + 1} 个服务档位缺少有效的 match 标识")
            svc_tiers.append({
                "match": match_val.strip(),
                "input_multiplier": _optional_multiplier(tier.get("input_multiplier"), field_label="输入倍率"),
                "input_cached_multiplier": _optional_multiplier(tier.get("input_cached_multiplier"), field_label="缓存读倍率"),
                "cache_creation_multiplier": _optional_multiplier(tier.get("cache_creation_multiplier"), field_label="缓存写倍率"),
                "output_multiplier": _optional_multiplier(tier.get("output_multiplier"), field_label="输出倍率"),
                "input": _optional_price(tier.get("input"), field_label="服务档位输入单价"),
                "input_cached": _optional_price(tier.get("input_cached"), field_label="服务档位缓存读单价"),
                "cache_creation": _optional_price(tier.get("cache_creation"), field_label="服务档位缓存写单价"),
                "output": _optional_price(tier.get("output"), field_label="服务档位输出单价"),
            })
        return {
            "mode": "per_tier",
            "currency": currency,
            "base": {
                "input": base_in,
                "input_cached": base_cached,
                "cache_creation": base_create,
                "output": base_out,
            },
            "context_tiers": ctx_tiers,
            "service_tiers": svc_tiers,
            "multiplier": multiplier,
        }

    if mode == "tiered_expr":
        allowed = {"mode", "currency", "expr", "tiers", "default_expr", "cluster_multiplier"}
        if set(raw) - allowed:
            raise PricingSettingsError("pricing_config_invalid", "tiered_expr 规则包含不支持的字段")
        try:
            normalized = normalize_tiered_expr_rule(raw, max_multiplier=MAX_MULTIPLIER)
        except PricingExprError as error:
            raise PricingSettingsError("pricing_expr_invalid", error.reason) from error
        validation = validate_tiered_expr_rule(normalized)
        if not validation["valid"]:
            raise PricingSettingsError("pricing_expr_invalid", validation["error"] or "表达式验证失败")
        return normalized

    # per_turn or per_request
    allowed = {"mode", "currency", "price", "multiplier"}
    if set(raw) - allowed:
        raise PricingSettingsError("pricing_config_invalid", f"{mode} 规则包含不支持的字段")
    currency = raw.get("currency", "USD")
    if currency not in SUPPORTED_CURRENCIES:
        raise PricingSettingsError("pricing_config_invalid", "单价币种仅支持 USD 或 CNY")
    price = _optional_price(raw.get("price"), field_label="固定单价")
    if price is None:
        raise PricingSettingsError("pricing_config_invalid", f"{mode} 必须填写固定单价")
    multiplier = round(
        _finite_number(
            raw.get("multiplier", 1.0),
            minimum=MIN_V2_MULTIPLIER,
            maximum=MAX_MULTIPLIER,
            code="pricing_config_invalid",
            reason="倍率必须在 0 到 100 之间",
        ),
        6,
    )
    return {
        "mode": mode,
        "currency": currency,
        "price": price,
        "multiplier": multiplier,
    }


def _validate_exchange_rate(raw: object, current: dict[str, Any]) -> dict[str, Any]:
    if type(raw) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "汇率配置必须是对象")
    allowed = {"mode", "manual_usd_cny", "cached_usd_cny", "fallback_usd_cny", "fetched_at", "source"}
    if set(raw) - allowed:
        raise PricingSettingsError("pricing_config_invalid", "汇率配置包含不支持的字段")
    mode = raw.get("mode", current["mode"])
    if mode not in RATE_MODES:
        raise PricingSettingsError("pricing_config_invalid", "汇率模式仅支持 auto 或 manual")
    manual_raw = raw.get("manual_usd_cny", current["manual_usd_cny"])
    if manual_raw is None or manual_raw == "":
        manual = None
    else:
        manual = round(
            _finite_number(
                manual_raw,
                minimum=MIN_USD_CNY,
                maximum=MAX_USD_CNY,
                code="pricing_config_invalid",
                reason="手动汇率必须在 1.0 到 100.0 之间",
            ),
            6,
        )
    if mode == "manual" and manual is None:
        raise PricingSettingsError("pricing_config_invalid", "手动汇率模式需要填写有效的 USD→CNY 汇率")
    cached = round(
        _finite_number(
            raw.get("cached_usd_cny", current["cached_usd_cny"]),
            minimum=MIN_USD_CNY,
            maximum=MAX_USD_CNY,
            code="pricing_config_invalid",
            reason="缓存汇率超出有效范围",
        ),
        6,
    )
    fallback = round(
        _finite_number(
            raw.get("fallback_usd_cny", current["fallback_usd_cny"]),
            minimum=MIN_USD_CNY,
            maximum=MAX_USD_CNY,
            code="pricing_config_invalid",
            reason="离线默认汇率必须在 1.0 到 100.0 之间",
        ),
        6,
    )
    fetched_at = _validate_iso_timestamp(
        raw.get("fetched_at", current["fetched_at"]),
        code="pricing_config_invalid",
        label="汇率更新时间",
    )
    source = raw.get("source", current["source"])
    if type(source) is not str or not source or _RATE_SOURCE_PATTERN.fullmatch(source) is None:
        raise PricingSettingsError("pricing_config_invalid", "汇率来源标识无效")
    return {
        "mode": mode,
        "manual_usd_cny": manual,
        "cached_usd_cny": cached,
        "fallback_usd_cny": fallback,
        "fetched_at": fetched_at,
        "source": source,
    }


def _validate_multipliers(raw: object) -> dict[str, float]:
    if raw is None:
        return {}
    if type(raw) is not dict or len(raw) > MAX_PRICING_MULTIPLIERS:
        raise PricingSettingsError("pricing_config_invalid", f"聚类倍率不能超过 {MAX_PRICING_MULTIPLIERS} 条")
    result: dict[str, float] = {}
    for cid, val in raw.items():
        clean_id = _validate_rule_key(cid, label="聚类标识")
        if val is None or val == "":
            continue
        mult = round(
            _finite_number(
                val,
                minimum=MIN_V2_MULTIPLIER,
                maximum=MAX_MULTIPLIER,
                code="pricing_config_invalid",
                reason="聚类倍率必须在 0 到 100 之间",
            ),
            6,
        )
        # 1.0 is the neutral identity; omit it cleanly so UI badges stay accurate.
        if mult != 1.0:
            result[clean_id] = mult
    return result


def _validate_schedules(raw: object, *, default_timezone: str) -> dict[str, dict[str, Any]]:
    if raw is None:
        return {}
    if type(raw) is not dict or len(raw) > MAX_PRICING_SCHEDULES:
        raise PricingSettingsError("pricing_config_invalid", f"分时定价不能超过 {MAX_PRICING_SCHEDULES} 条")
    result: dict[str, dict[str, Any]] = {}
    for key, sched_raw in raw.items():
        clean_key = _validate_rule_key(key, label="分时定价标识")
        if sched_raw is None:
            continue
        try:
            normalized = normalize_schedule(
                sched_raw,
                rule_validator=validate_pricing_rule,
                default_timezone=default_timezone,
            )
        except PricingScheduleError as error:
            raise PricingSettingsError(error.code, error.reason) from error
        result[clean_key] = normalized
    return result


def _is_valid_source_id(sid: object, builtin_ids: dict[str, Any] | set[str]) -> bool:
    if type(sid) is not str:
        return False
    if sid in builtin_ids:
        return True
    if sid.startswith("newapi:"):
        if sid != sid.strip() or ".." in sid:
            return False
        suffix = sid.removeprefix("newapi:")
        if not suffix.strip() or len(suffix) > MAX_PROVIDER_ID_CHARS:
            return False
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in sid):
            return False
        return True
    return _SOURCE_ID_PATTERN.fullmatch(sid) is not None


def _validate_price_sources(raw: object) -> dict[str, dict[str, Any]]:
    base = default_price_sources()
    if raw is None:
        return base
    if type(raw) is not dict or len(raw) > 128:
        raise PricingSettingsError("pricing_config_invalid", "价格源配置格式无效")
    allowed = {
        "enabled",
        "kind",
        "label",
        "url",
        "provider_id",
        "currency_override",
        "fiat_per_base_unit",
        "auth_mode",
        "cluster_id",
    }
    for sid, cfg in raw.items():
        if not _is_valid_source_id(sid, base):
            raise PricingSettingsError("pricing_config_invalid", "价格源标识无效")
        if type(cfg) is not dict or (set(cfg) - allowed):
            raise PricingSettingsError("pricing_config_invalid", f"价格源 {sid} 包含不支持的字段")
        enabled = cfg.get("enabled", True)
        if type(enabled) is not bool:
            raise PricingSettingsError("pricing_config_invalid", f"价格源 {sid} 启用开关必须是布尔值")
        if sid in base:
            base[sid]["enabled"] = enabled
        else:
            if not sid.startswith("newapi:"):
                raise PricingSettingsError("pricing_config_invalid", f"未知价格源: {sid}")
            url = str(cfg.get("url") or "").strip()
            if url and not (url.startswith("http://") or url.startswith("https://")):
                raise PricingSettingsError("pricing_config_invalid", "New API 价格源地址必须为 HTTP(S)")
            entry: dict[str, Any] = {
                "enabled": enabled,
                "kind": "newapi",
                "label": str(cfg.get("label") or sid).strip()[:80] or sid,
                "url": url[:512],
                "provider_id": str(cfg.get("provider_id") or sid.removeprefix("newapi:")).strip()[:MAX_PROVIDER_ID_CHARS],
            }
            raw_cur_ovr = cfg.get("currency_override")
            if raw_cur_ovr is not None and raw_cur_ovr != "":
                if type(raw_cur_ovr) is not str or raw_cur_ovr not in ("auto", "CNY", "USD"):
                    raise PricingSettingsError("pricing_config_invalid", f"价格源 {sid} 币种覆盖仅支持 auto、CNY 或 USD")
                if raw_cur_ovr in ("CNY", "USD"):
                    entry["currency_override"] = raw_cur_ovr
            raw_fiat_rate = cfg.get("fiat_per_base_unit")
            if raw_fiat_rate is not None and raw_fiat_rate != "":
                fiat_rate = _finite_number(
                    raw_fiat_rate,
                    minimum=1e-6,
                    maximum=100_000.0,
                    code="pricing_config_invalid",
                    reason=f"价格源 {sid} 的法币折算单价必须在 0 到 100000 之间",
                )
                entry["fiat_per_base_unit"] = round(fiat_rate, 6)
            raw_auth = cfg.get("auth_mode")
            if raw_auth is not None and raw_auth != "":
                if type(raw_auth) is not str or raw_auth not in ("anonymous", "bearer"):
                    raise PricingSettingsError("pricing_config_invalid", f"价格源 {sid} 鉴权模式无效")
                entry["auth_mode"] = raw_auth
            raw_cluster = cfg.get("cluster_id")
            if raw_cluster is not None and raw_cluster != "":
                if type(raw_cluster) is not str or len(raw_cluster.strip()) > MAX_PROVIDER_ID_CHARS:
                    raise PricingSettingsError("pricing_config_invalid", f"价格源 {sid} 聚类标识无效")
                clean_cluster = raw_cluster.strip()
                if clean_cluster:
                    entry["cluster_id"] = clean_cluster
            base[sid] = entry
    return base


def _validate_price_selections(raw: object) -> dict[str, dict[str, str]]:
    if raw is None:
        return {}
    if type(raw) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "价格源选择配置必须是对象")
    result: dict[str, dict[str, str]] = {}
    total_entries = 0
    for pid, model_map in raw.items():
        clean_pid = _validate_rule_key(pid, label="Provider 标识")
        if type(model_map) is not dict:
            raise PricingSettingsError("pricing_config_invalid", "模型价格源选择必须是对象")
        clean_models: dict[str, str] = {}
        for model_name, price_key in model_map.items():
            if type(model_name) is not str or not model_name.strip() or len(model_name) > MAX_PROVIDER_ID_CHARS:
                raise PricingSettingsError("pricing_config_invalid", "价格源选择中的模型名无效")
            if type(price_key) is not str or not price_key.strip() or len(price_key) > 256:
                raise PricingSettingsError("pricing_config_invalid", "价格源选择中的 price_key 无效")
            total_entries += 1
            if total_entries > MAX_PRICE_SELECTIONS:
                raise PricingSettingsError("pricing_config_invalid", f"价格源选择总数不能超过 {MAX_PRICE_SELECTIONS} 条")
            clean_models[model_name.strip()] = price_key.strip()
        if clean_models:
            result[clean_pid] = clean_models
    return result


def _validate_price_sync(raw: object, current: dict[str, Any]) -> dict[str, Any]:
    if raw is None:
        return dict(current)
    if type(raw) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "价格同步配置必须是对象")
    allowed = {"auto_daily", "last_auto_sync_at"}
    if set(raw) - allowed:
        raise PricingSettingsError("pricing_config_invalid", "价格同步配置包含不支持的字段")
    auto_daily = raw.get("auto_daily", current.get("auto_daily", True))
    if type(auto_daily) is not bool:
        raise PricingSettingsError("pricing_config_invalid", "每日自动同步开关必须是布尔值")
    last_sync = _validate_iso_timestamp(
        raw.get("last_auto_sync_at", current.get("last_auto_sync_at")),
        code="pricing_config_invalid",
        label="自动同步时间",
    )
    return {
        "auto_daily": auto_daily,
        "last_auto_sync_at": last_sync,
    }


def _validate_document(data: object) -> dict[str, Any]:
    defaults = default_pricing_settings()
    if type(data) is not dict:
        raise PricingSettingsError("pricing_config_invalid", "模型计价配置格式无效")
    allowed = {
        "version",
        "display_currency",
        "use_builtin_presets",
        "exchange_rate",
        "provider_tariffs",
        "pricing_rules",
        "pricing_multipliers",
        "pricing_schedules",
        "pricing_timezone",
        "price_sources",
        "price_selections",
        "price_sync",
        "revision",
    }
    if set(data) - allowed:
        raise PricingSettingsError("pricing_config_invalid", "模型计价配置包含不支持的字段")

    display_currency = data.get("display_currency", defaults["display_currency"])
    if display_currency not in SUPPORTED_CURRENCIES:
        raise PricingSettingsError("pricing_config_invalid", "显示币种仅支持 CNY 或 USD")
    use_builtin_presets = data.get("use_builtin_presets", defaults["use_builtin_presets"])
    if type(use_builtin_presets) is not bool:
        raise PricingSettingsError("pricing_config_invalid", "内置参考价开关必须是布尔值")

    try:
        pricing_timezone = validate_timezone(data.get("pricing_timezone", defaults["pricing_timezone"]))
    except PricingScheduleError as error:
        raise PricingSettingsError(error.code, error.reason) from error

    exchange_rate = _validate_exchange_rate(
        data.get("exchange_rate", defaults["exchange_rate"]),
        defaults["exchange_rate"],
    )

    # Validate v1 provider_tariffs
    raw_tariffs = data.get("provider_tariffs", {})
    if type(raw_tariffs) is not dict or len(raw_tariffs) > MAX_PROVIDER_TARIFFS:
        raise PricingSettingsError("pricing_config_invalid", f"单价规则不能超过 {MAX_PROVIDER_TARIFFS} 条")
    provider_tariffs: dict[str, dict[str, Any]] = {}
    for pid, rule in raw_tariffs.items():
        clean_id, validated = _validate_provider_tariff(pid, rule)
        provider_tariffs[clean_id] = validated

    # Validate v2 pricing_rules (with seamless v1 -> v2 auto-migration when absent)
    raw_rules = data.get("pricing_rules")
    pricing_rules: dict[str, dict[str, Any]] = {}
    if raw_rules is None:
        for pid, v1_rule in provider_tariffs.items():
            pricing_rules[pid] = _v1_tariff_to_v2_rule(v1_rule)
    else:
        if type(raw_rules) is not dict or len(raw_rules) > MAX_PRICING_RULES:
            raise PricingSettingsError("pricing_config_invalid", f"计价规则不能超过 {MAX_PRICING_RULES} 条")
        for key, rule in raw_rules.items():
            clean_key = _validate_rule_key(key)
            pricing_rules[clean_key] = validate_pricing_rule(rule)

    pricing_multipliers = _validate_multipliers(data.get("pricing_multipliers"))
    pricing_schedules = _validate_schedules(
        data.get("pricing_schedules"), default_timezone=pricing_timezone
    )
    price_sources = _validate_price_sources(data.get("price_sources"))
    price_selections = _validate_price_selections(data.get("price_selections"))
    price_sync = _validate_price_sync(data.get("price_sync"), defaults["price_sync"])

    revision = data.get("revision")
    if type(revision) is not str or not revision:
        revision = hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    return {
        "version": 2,
        "display_currency": display_currency,
        "use_builtin_presets": use_builtin_presets,
        "exchange_rate": exchange_rate,
        "provider_tariffs": provider_tariffs,
        "pricing_rules": pricing_rules,
        "pricing_multipliers": pricing_multipliers,
        "pricing_schedules": pricing_schedules,
        "pricing_timezone": pricing_timezone,
        "price_sources": price_sources,
        "price_selections": price_selections,
        "price_sync": price_sync,
        "revision": revision,
    }


def effective_usd_cny_rate(settings: dict[str, Any] | None) -> float:
    fx = (settings or {}).get("exchange_rate") or {}
    if fx.get("mode") == "manual" and type(fx.get("manual_usd_cny")) in (int, float):
        rate = float(fx["manual_usd_cny"])
        if math.isfinite(rate) and MIN_USD_CNY <= rate <= MAX_USD_CNY:
            return rate
    for key in ("cached_usd_cny", "fallback_usd_cny"):
        val = fx.get(key)
        if type(val) in (int, float) and math.isfinite(float(val)) and MIN_USD_CNY <= float(val) <= MAX_USD_CNY:
            return float(val)
    return DEFAULT_FALLBACK_USD_CNY


def effective_rate_source(settings: dict[str, Any] | None) -> str:
    fx = (settings or {}).get("exchange_rate") or {}
    if fx.get("mode") == "manual" and fx.get("manual_usd_cny") is not None:
        return "manual"
    source = fx.get("source")
    return str(source) if type(source) is str and source else "static_fallback"


def is_rate_stale(
    settings: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    ttl_seconds: int = EXCHANGE_RATE_TTL_SECONDS,
) -> bool:
    fx = (settings or {}).get("exchange_rate") or {}
    if fx.get("mode") != "auto":
        return False
    fetched_at = fx.get("fetched_at")
    if type(fetched_at) is not str or not fetched_at:
        return True
    try:
        parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - parsed).total_seconds() >= ttl_seconds


def is_catalog_sync_due(
    settings: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    ttl_seconds: int = CATALOG_DAILY_SYNC_SECONDS,
) -> bool:
    sync_cfg = (settings or {}).get("price_sync") or {}
    if not sync_cfg.get("auto_daily", True):
        return False
    last_at = sync_cfg.get("last_auto_sync_at")
    if type(last_at) is not str or not last_at:
        return True
    try:
        parsed = datetime.fromisoformat(last_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (current - parsed).total_seconds() >= ttl_seconds


def convert_microusd(microusd: int | None, currency: str, usd_cny_rate: float) -> float | None:
    """Convert canonical microUSD integer to major currency units (USD or CNY)."""
    if microusd is None:
        return None
    if isinstance(microusd, bool) or type(microusd) not in (int, float) or not math.isfinite(float(microusd)):
        raise ValueError("microusd must be a finite number or None")
    if currency not in SUPPORTED_CURRENCIES:
        raise ValueError("unsupported display currency")
    usd = float(microusd) / 1_000_000.0
    if currency == "USD":
        return usd
    if (
        isinstance(usd_cny_rate, bool)
        or not math.isfinite(float(usd_cny_rate))
        or not (MIN_USD_CNY <= float(usd_cny_rate) <= MAX_USD_CNY)
    ):
        raise ValueError("usd_cny_rate must be bounded")
    return usd * float(usd_cny_rate)


def _convert_price_between_currencies(
    value: float,
    *,
    from_currency: str,
    to_currency: str,
    usd_cny_rate: float,
) -> float:
    if from_currency == to_currency:
        return round(float(value), 6)
    if from_currency == "USD" and to_currency == "CNY":
        return round(float(value) * usd_cny_rate, 6)
    if from_currency == "CNY" and to_currency == "USD":
        return round(float(value) / usd_cny_rate, 6)
    return round(float(value), 6)


def _extract_four_rates(rule: dict[str, Any] | None) -> tuple[dict[str, float | None], str] | None:
    if not isinstance(rule, dict):
        return None
    mode = rule.get("mode", "per_token")
    currency = str(rule.get("currency") or "USD")
    if mode == "per_token":
        mult = float(rule.get("multiplier", 1.0))
        if rule.get("free") is True or mult == 0.0:
            return {"input": 0.0, "input_cached": 0.0, "cache_creation": 0.0, "output": 0.0}, currency
        return {
            "input": (round(float(rule["input"]) * mult, 6) if rule.get("input") is not None else None),
            "input_cached": (round(float(rule["input_cached"]) * mult, 6) if rule.get("input_cached") is not None else None),
            "cache_creation": (round(float(rule["cache_creation"]) * mult, 6) if rule.get("cache_creation") is not None else None),
            "output": (round(float(rule["output"]) * mult, 6) if rule.get("output") is not None else None),
        }, currency
    if mode == "per_tier" and isinstance(rule.get("base"), dict):
        base = rule["base"]
        mult = float(rule.get("multiplier", 1.0))
        return {
            "input": (round(float(base["input"]) * mult, 6) if base.get("input") is not None else None),
            "input_cached": (round(float(base["input_cached"]) * mult, 6) if base.get("input_cached") is not None else None),
            "cache_creation": (round(float(base["cache_creation"]) * mult, 6) if base.get("cache_creation") is not None else None),
            "output": (round(float(base["output"]) * mult, 6) if base.get("output") is not None else None),
        }, currency
    return None


def _rule_needs_fallback(user_rule: dict[str, Any]) -> bool:
    if not isinstance(user_rule, dict) or user_rule.get("mode") != "per_token":
        return False
    if user_rule.get("free") is True:
        return False
    cfg = user_rule.get("configured") if isinstance(user_rule.get("configured"), dict) else {}
    return any(
        not (cfg.get(field) if field in cfg else (user_rule.get(field) is not None))
        for field in ("input", "input_cached", "cache_creation", "output")
    )


def _inherit_per_token(
    user_rule: dict[str, Any],
    fallback_rule: dict[str, Any] | None,
    *,
    usd_cny_rate: float,
) -> dict[str, Any] | None:
    """Fill unconfigured (blank) ``per_token`` fields from fallback with cross-currency conversion.

    Explicitly configured fields — including explicit ``0`` (free) — are NEVER overwritten.
    ``cache_creation`` stays ``None`` when neither user nor fallback configures it (billing falls
    back to ``input``).
    """
    result = deepcopy(user_rule)
    if result.get("mode") != "per_token":
        return result
    if result.get("free") is True:
        return result

    cfg = result.get("configured") if isinstance(result.get("configured"), dict) else {}
    target_currency = str(result.get("currency") or "USD")
    fb = _extract_four_rates(fallback_rule)
    if fb is not None:
        fb_rates, fb_currency = fb
        for field in ("input", "input_cached", "cache_creation", "output"):
            is_configured = cfg.get(field) if field in cfg else (result.get(field) is not None)
            if is_configured:
                continue
            fb_val = fb_rates.get(field)
            if fb_val is not None:
                result[field] = _convert_price_between_currencies(
                    fb_val,
                    from_currency=fb_currency,
                    to_currency=target_currency,
                    usd_cny_rate=usd_cny_rate,
                )

    if result.get("input") is None or result.get("output") is None:
        return None
    return result


def _apply_multiplier_to_rule(rule: dict[str, Any], multiplier: float) -> dict[str, Any]:
    """Deep-copy and scale any 5-mode rule by ``multiplier`` (explicit ``0.0`` = free)."""
    out = deepcopy(rule)
    mult = float(multiplier)
    if mult == 1.0:
        return out
    mode = out.get("mode", "per_token")

    if mode == "per_token":
        if mult == 0.0:
            out["input"] = 0.0
            out["input_cached"] = 0.0
            out["cache_creation"] = 0.0
            out["output"] = 0.0
            out["free"] = True
            return out
        for field in ("input", "input_cached", "cache_creation", "output"):
            if out.get(field) is not None:
                out[field] = round(float(out[field]) * mult, 6)
        return out

    if mode == "per_tier":
        base = out.get("base")
        if isinstance(base, dict):
            for field in ("input", "input_cached", "cache_creation", "output"):
                if base.get(field) is not None:
                    base[field] = round(float(base[field]) * mult, 6)
        for tier in out.get("context_tiers") or ():
            if not isinstance(tier, dict):
                continue
            for field in ("input", "input_cached", "cache_creation", "output"):
                if tier.get(field) is not None:
                    tier[field] = round(float(tier[field]) * mult, 6)
        for stier in out.get("service_tiers") or ():
            if not isinstance(stier, dict):
                continue
            # Scale ONLY absolute fallback prices; preserve *_multiplier untouched!
            for field in ("input", "input_cached", "cache_creation", "output"):
                if stier.get(field) is not None:
                    stier[field] = round(float(stier[field]) * mult, 6)
        return out

    if mode == "tiered_expr":
        prev = float(out.get("cluster_multiplier", 1.0))
        out["cluster_multiplier"] = round(prev * mult, 6)
        return out

    if mode in ("per_turn", "per_request"):
        if out.get("price") is not None:
            out["price"] = round(float(out["price"]) * mult, 6)
        return out

    return out


def _enabled_source_ids(settings: dict[str, Any]) -> set[str]:
    sources_cfg = settings.get("price_sources")
    if not isinstance(sources_cfg, dict):
        return set(PUBLIC_SOURCE_IDS)
    return {
        sid
        for sid, cfg in sources_cfg.items()
        if isinstance(cfg, dict) and cfg.get("enabled", True) is True
    }


def _best_match_legacy_key(model_name: str, rules: dict[str, dict[str, Any]]) -> str | None:
    """Fuzzy match a bare (non-'|') key in ``pricing_rules`` against ``model_name``."""
    model_text = str(model_name or "").strip()
    if not model_text or not rules:
        return None
    bare_keys = [k for k in rules if "|" not in k]
    if not bare_keys:
        return None
    lower_map = {k.lower(): k for k in bare_keys}
    if model_text.lower() in lower_map:
        return lower_map[model_text.lower()]

    norm_slug = _slug_key(_normalize_model_name(model_text))
    if not norm_slug:
        return None
    slug_map: list[tuple[str, str]] = []
    for key in bare_keys:
        key_slug = _slug_key(_normalize_model_name(key))
        if key_slug:
            slug_map.append((key_slug, key))
    for key_slug, key in slug_map:
        if key_slug == norm_slug:
            return key
    # Clean boundary prefix match, longest first
    slug_map.sort(key=lambda pair: len(pair[0]), reverse=True)
    for key_slug, key in slug_map:
        if norm_slug.startswith(key_slug + "-"):
            return key
    return None


def _resolve_non_user_fallback(
    provider_id: str,
    model_name: str,
    settings: dict[str, Any],
    catalog: dict[str, Any] | None,
    *,
    own_source_ids: frozenset[str] = frozenset(),
) -> EffectivePricingRule | None:
    """Resolve Tier 2 (selection) -> Tier 4 (auto catalog) -> Tier 5 (built-in preset)."""
    pid = str(provider_id or "").strip()
    model = str(model_name or "").strip()
    enabled_sources = _enabled_source_ids(settings)

    # Tier 2: Persisted catalog selection
    selections = settings.get("price_selections") or {}
    if pid and model and isinstance(selections.get(pid), dict):
        price_key = selections[pid].get(model)
        if type(price_key) is str and price_key and isinstance(catalog, dict):
            entry = (catalog.get("prices") or {}).get(price_key)
            if isinstance(entry, dict) and isinstance(entry.get("rule"), dict):
                return EffectivePricingRule(
                    rule=deepcopy(entry["rule"]),
                    source="catalog_selected",
                    price_key=price_key,
                    source_id=str(entry.get("source_id") or ""),
                    source_label=str(entry.get("source_label") or entry.get("source_id") or ""),
                    currency=str(entry["rule"].get("currency") or "USD"),
                    matched_key=price_key,
                )

    # Tier 4: Auto-matched catalog candidate (score >= 0.85, not persisted)
    if model and isinstance(catalog, dict) and catalog.get("prices"):
        candidates = find_candidates(
            model,
            catalog=catalog,
            provider_id=pid,
            own_source_ids=own_source_ids,
            enabled_sources=enabled_sources,
        )
        auto = select_auto_candidate(candidates)
        if auto is not None:
            return EffectivePricingRule(
                rule=deepcopy(auto.rule),
                source="catalog_auto",
                price_key=auto.price_key,
                source_id=auto.source_id,
                source_label=auto.source_label,
                currency=str(auto.rule.get("currency") or "USD"),
                matched_key=auto.price_key,
            )

    # Tier 5: Built-in reference presets
    if settings.get("use_builtin_presets", True):
        preset = match_builtin_tariff(model) if model else match_builtin_tariff(pid)
        if preset is not None:
            return EffectivePricingRule(
                rule=preset.to_rule(),
                source="builtin_preset",
                preset_id=preset.preset_id,
                currency="USD",
                matched_key=preset.preset_id,
            )

    return None


def resolve_pricing_rule(
    provider_id: str,
    model: str = "",
    settings: dict[str, Any] | None = None,
    *,
    catalog: dict[str, Any] | None = None,
    legacy_tariff: UsageTariff | None = None,
    own_source_ids: frozenset[str] = frozenset(),
) -> EffectivePricingRule | None:
    """Resolve the 6-tier base pricing rule (before schedule overlay & cluster multiplier).

    Precedence:
      1. Exact model override ``pricing_rules["provider_id|model"]`` (+ ``_inherit_per_token``)
      2. Persisted catalog selection ``price_selections[provider_id][model]``
      3. Legacy / provider override ``pricing_rules[provider_id]`` (or fuzzy non-``|`` rule, or ``legacy_tariff``)
      4. Auto-matched catalog candidate (``score >= 0.85``, unpersisted)
      5. Built-in default preset (``use_builtin_presets=True``)
      6. ``None`` (unpriced)
    """
    cfg = settings if isinstance(settings, dict) else default_pricing_settings()
    pid = str(provider_id or "").strip()
    model_text = str(model or "").strip()
    usd_cny_rate = effective_usd_cny_rate(cfg)

    rules = cfg.get("pricing_rules")
    if not isinstance(rules, dict):
        rules = {}
    # Compatibility: if caller constructed an ad-hoc v1 dict with only provider_tariffs
    if not rules and isinstance(cfg.get("provider_tariffs"), dict):
        rules = {k: _v1_tariff_to_v2_rule(v) for k, v in cfg["provider_tariffs"].items()}

    # Tier 1: Exact "provider_id|model" override
    exact_key = f"{pid}|{model_text}" if (pid and model_text) else ""
    if exact_key and exact_key in rules:
        raw_rule = rules[exact_key]
        fallback = (
            _resolve_non_user_fallback(
                pid, model_text, cfg, catalog, own_source_ids=own_source_ids
            )
            if _rule_needs_fallback(raw_rule)
            else None
        )
        merged = _inherit_per_token(
            raw_rule,
            fallback.rule if fallback is not None else None,
            usd_cny_rate=usd_cny_rate,
        )
        if merged is not None:
            is_free = merged.get("mode") == "per_token" and merged.get("free") is True
            return EffectivePricingRule(
                rule=merged,
                source="provider_free" if is_free else "model_override",
                currency=str(merged.get("currency") or "USD"),
                matched_key=exact_key,
            )

    # Tier 2: Persisted catalog selection
    selections = cfg.get("price_selections") or {}
    if pid and model_text and isinstance(selections.get(pid), dict):
        price_key = selections[pid].get(model_text)
        if type(price_key) is str and price_key and isinstance(catalog, dict):
            entry = (catalog.get("prices") or {}).get(price_key)
            if isinstance(entry, dict) and isinstance(entry.get("rule"), dict):
                return EffectivePricingRule(
                    rule=deepcopy(entry["rule"]),
                    source="catalog_selected",
                    price_key=price_key,
                    source_id=str(entry.get("source_id") or ""),
                    source_label=str(entry.get("source_label") or entry.get("source_id") or ""),
                    currency=str(entry["rule"].get("currency") or "USD"),
                    matched_key=price_key,
                )

    # Tier 3: Provider-level override (exact provider_id, then fuzzy bare-key match on model)
    tier3_key: str | None = None
    if pid and pid in rules:
        tier3_key = pid
    elif model_text:
        tier3_key = _best_match_legacy_key(model_text, rules)

    if tier3_key is not None:
        raw_rule = rules[tier3_key]
        fallback = (
            _resolve_non_user_fallback(
                pid, model_text, cfg, catalog, own_source_ids=own_source_ids
            )
            if _rule_needs_fallback(raw_rule)
            else None
        )
        merged = _inherit_per_token(
            raw_rule,
            fallback.rule if fallback is not None else None,
            usd_cny_rate=usd_cny_rate,
        )
        if merged is not None:
            is_free = merged.get("mode") == "per_token" and merged.get("free") is True
            return EffectivePricingRule(
                rule=merged,
                source="provider_free" if is_free else "provider_override",
                currency=str(merged.get("currency") or "USD"),
                matched_key=tier3_key,
            )

    # Legacy host config tariff compatibility (non-zero static config)
    if (
        legacy_tariff is not None
        and legacy_tariff.configured
        and any((
            legacy_tariff.input,
            legacy_tariff.cached_input,
            legacy_tariff.output,
            legacy_tariff.cache_creation or 0,
        ))
    ):
        legacy_rule = {
            "mode": "per_token",
            "currency": "USD",
            "input": round(legacy_tariff.input / 1_000_000, 6),
            "input_cached": round(legacy_tariff.cached_input / 1_000_000, 6),
            "cache_creation": (
                round(legacy_tariff.cache_creation / 1_000_000, 6)
                if legacy_tariff.cache_creation is not None
                else None
            ),
            "output": round(legacy_tariff.output / 1_000_000, 6),
            "multiplier": 1.0,
            "free": False,
            "configured": {
                "input": True,
                "input_cached": True,
                "cache_creation": legacy_tariff.cache_creation is not None,
                "output": True,
            },
        }
        return EffectivePricingRule(
            rule=legacy_rule,
            source="legacy_config",
            currency="USD",
        )

    # Tier 4 (auto catalog) & Tier 5 (built-in preset)
    return _resolve_non_user_fallback(
        pid, model_text, cfg, catalog, own_source_ids=own_source_ids
    )


def lookup_pricing_schedule(
    provider_id: str,
    model: str,
    settings: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve schedule precedence: ``schedules["provider_id|model"]`` > ``schedules[provider_id]``."""
    schedules = (settings or {}).get("pricing_schedules")
    if not isinstance(schedules, dict) or not schedules:
        return None
    pid = str(provider_id or "").strip()
    model_text = str(model or "").strip()
    if pid and model_text:
        exact = schedules.get(f"{pid}|{model_text}")
        if isinstance(exact, dict):
            return exact
    if pid:
        prov = schedules.get(pid)
        if isinstance(prov, dict):
            return prov
    return None


def lookup_cluster_multiplier(
    provider_id: str,
    settings: dict[str, Any] | None,
    *,
    cluster_id: str | None = None,
) -> float:
    """Resolve cluster multiplier keyed by ``provider_source_id`` (fallback to ``provider_id``)."""
    multipliers = (settings or {}).get("pricing_multipliers")
    if not isinstance(multipliers, dict) or not multipliers:
        return 1.0
    cid = str(cluster_id or "").strip()
    pid = str(provider_id or "").strip()
    if cid and cid in multipliers:
        val = multipliers[cid]
        if type(val) in (int, float) and math.isfinite(float(val)):
            return float(val)
    if pid and pid in multipliers:
        val = multipliers[pid]
        if type(val) in (int, float) and math.isfinite(float(val)):
            return float(val)
    return 1.0


_BASE_UNSET = object()


def resolve_effective_pricing(
    provider_id: str,
    model: str = "",
    settings: dict[str, Any] | None = None,
    *,
    catalog: dict[str, Any] | None = None,
    legacy_tariff: UsageTariff | None = None,
    cluster_id: str | None = None,
    timestamp: object = None,
    own_source_ids: frozenset[str] = frozenset(),
    precomputed_base: Any = _BASE_UNSET,
) -> EffectivePricingRule | None:
    """Full Pricing v2 resolution: 6-tier base -> schedule overlay -> cluster multiplier (last!)."""
    cfg = settings if isinstance(settings, dict) else default_pricing_settings()
    usd_cny_rate = effective_usd_cny_rate(cfg)

    if precomputed_base is _BASE_UNSET:
        base = resolve_pricing_rule(
            provider_id,
            model,
            cfg,
            catalog=catalog,
            legacy_tariff=legacy_tariff,
            own_source_ids=own_source_ids,
        )
    else:
        base = precomputed_base

    active_rule: dict[str, Any] | None = deepcopy(base.rule) if base is not None else None
    source = base.source if base is not None else "unconfigured"
    preset_id = base.preset_id if base is not None else None
    price_key = base.price_key if base is not None else None
    source_id = base.source_id if base is not None else None
    source_label = base.source_label if base is not None else None
    matched_key = base.matched_key if base is not None else None
    schedule_period_id: str | None = None
    schedule_period_name: str | None = None

    # Orthogonal schedule overlay (only when timestamp is present!)
    schedule = lookup_pricing_schedule(provider_id, model, cfg)
    if schedule is not None and timestamp is not None:
        default_tz = str(cfg.get("pricing_timezone") or DEFAULT_PRICING_TIMEZONE)
        period = match_schedule_period(schedule, timestamp, default_timezone=default_tz)
        if period is not None:
            schedule_period_id = str(period.get("id") or "")
            schedule_period_name = str(period.get("name") or "")
            if period.get("mode") == "multiplier":
                if active_rule is not None:
                    # Explicit 0.0 = free! Never use `or 1.0`.
                    period_mult = float(period["multiplier"])
                    active_rule = _apply_multiplier_to_rule(active_rule, period_mult)
            elif period.get("mode") == "override":
                override_rule = deepcopy(period["override"])
                if override_rule.get("mode") == "per_token":
                    if "currency" not in override_rule and active_rule is not None:
                        override_rule["currency"] = active_rule.get("currency", "USD")
                    merged = _inherit_per_token(
                        override_rule,
                        active_rule,
                        usd_cny_rate=usd_cny_rate,
                    )
                    if merged is not None:
                        active_rule = merged
                        source = "schedule_override"
                else:
                    active_rule = override_rule
                    source = "schedule_override"

    if active_rule is None:
        return None

    # Cluster multiplier applied LAST (after schedule overlay!)
    cluster_mult = lookup_cluster_multiplier(provider_id, cfg, cluster_id=cluster_id)
    if cluster_mult != 1.0:
        active_rule = _apply_multiplier_to_rule(active_rule, cluster_mult)

    return EffectivePricingRule(
        rule=active_rule,
        source=source,
        preset_id=preset_id,
        price_key=price_key,
        source_id=source_id,
        source_label=source_label,
        schedule_period_id=schedule_period_id,
        schedule_period_name=schedule_period_name,
        cluster_multiplier=cluster_mult,
        currency=str(active_rule.get("currency") or "USD"),
        matched_key=matched_key,
    )


def provider_needs_per_call_repricing(
    provider_id: str,
    model: str,
    settings: dict[str, Any] | None,
    *,
    catalog: dict[str, Any] | None = None,
    cluster_id: str | None = None,
    own_source_ids: frozenset[str] = frozenset(),
) -> bool:
    """Return True iff repricing requires per-call timestamp/context evaluation."""
    cfg = settings if isinstance(settings, dict) else {}
    pid = str(provider_id or "").strip()
    schedule = lookup_pricing_schedule(pid, model, cfg)
    if schedule_is_active(schedule):
        return True
    if pid:
        prefix = pid + "|"
        # Check any model-scoped active schedules under this provider
        for key, sched in (cfg.get("pricing_schedules") or {}).items():
            if (key == pid or (type(key) is str and key.startswith(prefix))) and schedule_is_active(sched):
                return True
        # Check any provider or model-scoped per_tier / tiered_expr rules
        for key, rule in (cfg.get("pricing_rules") or {}).items():
            if (key == pid or (type(key) is str and key.startswith(prefix))) and isinstance(rule, dict):
                if rule.get("mode") in ("per_tier", "tiered_expr"):
                    return True
        # Check persisted catalog selections for any model under this provider
        sel_map = (cfg.get("price_selections") or {}).get(pid)
        if isinstance(sel_map, dict) and isinstance(catalog, dict):
            prices = catalog.get("prices") or {}
            for price_key in sel_map.values():
                entry = prices.get(price_key) if type(price_key) is str else None
                if isinstance(entry, dict) and isinstance(entry.get("rule"), dict):
                    if entry["rule"].get("mode") in ("per_tier", "tiered_expr"):
                        return True
    resolved = resolve_effective_pricing(
        pid,
        model,
        cfg,
        catalog=catalog,
        cluster_id=cluster_id,
        own_source_ids=own_source_ids,
    )
    if resolved is None:
        return False
    return resolved.mode in ("per_tier", "tiered_expr")


def _price_to_microusd(price: float, *, currency: str, multiplier: float, usd_cny_rate: float) -> int:
    usd_price = price if currency == "USD" else (price / usd_cny_rate)
    scaled = int(round(usd_price * multiplier * 1_000_000))
    return max(0, min(scaled, MAX_MICROUSD_RATE))


def _rule_to_usage_tariff(rule: dict[str, Any], *, usd_cny_rate: float) -> UsageTariff | None:
    mode = rule.get("mode", "per_token")
    currency = str(rule.get("currency") or "USD")
    mult = float(rule.get("multiplier", 1.0))

    if mode == "per_token":
        if rule.get("free") is True or mult == 0.0:
            return UsageTariff(0, 0, 0, cache_creation=0, configured=True)
        inp_val = rule.get("input")
        out_val = rule.get("output")
        if inp_val is None or out_val is None:
            return None
        inp = _price_to_microusd(float(inp_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        cached_val = rule.get("input_cached")
        cached = (
            inp
            if cached_val is None
            else _price_to_microusd(float(cached_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        )
        create_val = rule.get("cache_creation")
        creation = (
            None
            if create_val is None
            else _price_to_microusd(float(create_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        )
        out = _price_to_microusd(float(out_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        return UsageTariff(inp, cached, out, cache_creation=creation, configured=True)

    if mode == "per_tier":
        base = rule.get("base")
        if not isinstance(base, dict) or base.get("input") is None or base.get("output") is None:
            return None
        inp = _price_to_microusd(float(base["input"]), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        cached_val = base.get("input_cached")
        cached = (
            inp
            if cached_val is None
            else _price_to_microusd(float(cached_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        )
        create_val = base.get("cache_creation")
        creation = (
            None
            if create_val is None
            else _price_to_microusd(float(create_val), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        )
        out = _price_to_microusd(float(base["output"]), currency=currency, multiplier=mult, usd_cny_rate=usd_cny_rate)
        return UsageTariff(inp, cached, out, cache_creation=creation, configured=True)

    return None


def resolve_tariff(
    provider_id: str,
    model: str = "",
    settings: dict[str, Any] | None = None,
    *,
    legacy_tariff: UsageTariff | None = None,
    catalog: dict[str, Any] | None = None,
    cluster_id: str | None = None,
    timestamp: object = None,
    own_source_ids: frozenset[str] = frozenset(),
) -> ResolvedTariff | None:
    """Resolve a v1-compatible ``ResolvedTariff`` backed by the v2 6-tier engine."""
    cfg = settings if isinstance(settings, dict) else default_pricing_settings()
    rate = effective_usd_cny_rate(cfg)
    effective = resolve_effective_pricing(
        provider_id,
        model,
        cfg,
        catalog=catalog,
        legacy_tariff=legacy_tariff,
        cluster_id=cluster_id,
        timestamp=timestamp,
        own_source_ids=own_source_ids,
    )
    if effective is None:
        return None
    tariff = _rule_to_usage_tariff(effective.rule, usd_cny_rate=rate)
    if tariff is None:
        return None
    rule_mult = float(effective.rule.get("multiplier", 1.0)) * effective.cluster_multiplier
    return ResolvedTariff(
        tariff=tariff,
        source=effective.source,
        preset_id=effective.preset_id,
        currency=effective.currency,
        multiplier=round(rule_mult, 6),
    )


def estimate_cost_microusd(
    *,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    tariff: UsageTariff | None,
) -> int | None:
    """Compute local estimated cost in integer microUSD from token counts and UsageTariff."""
    if tariff is None or not tariff.configured:
        return None
    inp = int(input_tokens) if type(input_tokens) is int and input_tokens > 0 else 0
    out = int(output_tokens) if type(output_tokens) is int and output_tokens > 0 else 0
    cached = int(cached_input_tokens) if type(cached_input_tokens) is int and cached_input_tokens > 0 else 0
    creation = int(cache_creation_tokens) if type(cache_creation_tokens) is int and cache_creation_tokens > 0 else 0

    if input_tokens is None and output_tokens is None:
        if type(total_tokens) is not int or total_tokens < 0:
            return None
        if total_tokens == 0:
            return 0
        return min((total_tokens * tariff.input + 999999) // 1000000, MAX_MICROUSD_RATE)

    if inp == 0 and out == 0:
        if type(total_tokens) is int and total_tokens > 0:
            return min((total_tokens * tariff.input + 999999) // 1000000, MAX_MICROUSD_RATE)
        return 0

    billable_cached = min(inp, cached)
    remaining_input = inp - billable_cached
    billable_creation = min(remaining_input, creation)
    uncached = remaining_input - billable_creation
    cost = (
        uncached * tariff.input
        + billable_cached * tariff.cached_input
        + billable_creation * tariff.effective_cache_creation
        + out * tariff.output
        + 999999
    ) // 1000000
    return min(cost, MAX_MICROUSD_RATE)


def evaluate_call_cost_microusd(
    effective: EffectivePricingRule | dict[str, Any] | None,
    *,
    usd_cny_rate: float = DEFAULT_FALLBACK_USD_CNY,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    cache_creation_tokens: int | None = None,
    cache_creation_1h_tokens: int | None = None,
    output_tokens: int | None = None,
    total_tokens: int | None = None,
    service_tier: str = "",
    model: str = "",
    calls: int = 1,
    requests: int = 1,
    strict_expr: bool = False,
) -> int | None:
    """Evaluate local estimated cost in microUSD across all 5 Pricing v2 modes.

    Returns ``None`` when unpriced (or when ``tiered_expr`` fails with ``strict_expr=False``).
    Preserves explicit ``0`` for free rules/multipliers.
    """
    if effective is None:
        return None
    rule = effective.rule if isinstance(effective, EffectivePricingRule) else effective
    if not isinstance(rule, dict):
        return None

    mode = str(rule.get("mode") or "per_token")
    currency = str(rule.get("currency") or "USD")
    rule_mult = float(rule.get("multiplier", 1.0))

    if mode == "per_token":
        tariff = _rule_to_usage_tariff(rule, usd_cny_rate=usd_cny_rate)
        return estimate_cost_microusd(
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            cache_creation_tokens=cache_creation_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            tariff=tariff,
        )

    if mode == "per_turn":
        count = max(0, int(calls))
        price = rule.get("price")
        if price is None:
            return None
        usd_price = float(price) if currency == "USD" else (float(price) / usd_cny_rate)
        cost = int(round(count * usd_price * rule_mult * 1_000_000))
        return max(0, min(cost, MAX_MICROUSD_RATE))

    if mode == "per_request":
        req_count = max(0, int(requests))
        price = rule.get("price")
        if price is None:
            return None
        usd_price = float(price) if currency == "USD" else (float(price) / usd_cny_rate)
        cost = int(round(req_count * usd_price * rule_mult * 1_000_000))
        return max(0, min(cost, MAX_MICROUSD_RATE))

    # Normalize token breakdown for per_tier & tiered_expr
    inp = int(input_tokens) if type(input_tokens) is int and input_tokens > 0 else 0
    out = int(output_tokens) if type(output_tokens) is int and output_tokens > 0 else 0
    cached = int(cached_input_tokens) if type(cached_input_tokens) is int and cached_input_tokens > 0 else 0
    creation = int(cache_creation_tokens) if type(cache_creation_tokens) is int and cache_creation_tokens > 0 else 0
    creation_1h = (
        int(cache_creation_1h_tokens)
        if type(cache_creation_1h_tokens) is int and cache_creation_1h_tokens > 0
        else 0
    )

    if input_tokens is None and output_tokens is None:
        if type(total_tokens) is not int or total_tokens < 0:
            return None
        inp = total_tokens
        out = 0
        cached = 0
        creation = 0
        creation_1h = 0
    elif inp == 0 and out == 0 and type(total_tokens) is int and total_tokens > 0:
        inp = total_tokens

    billable_cached = min(inp, cached)
    remaining_input = inp - billable_cached
    billable_creation = min(remaining_input, creation)
    remaining_after_cc = remaining_input - billable_creation
    billable_cc1h = min(remaining_after_cc, creation_1h)
    uncached = remaining_after_cc - billable_cc1h
    total_in = uncached + billable_cached + billable_creation + billable_cc1h

    if mode == "per_tier":
        base = rule.get("base")
        if not isinstance(base, dict) or base.get("input") is None or base.get("output") is None:
            return None
        rates: dict[str, float | None] = {
            "input": float(base["input"]),
            "input_cached": float(base["input_cached"]) if base.get("input_cached") is not None else None,
            "cache_creation": float(base["cache_creation"]) if base.get("cache_creation") is not None else None,
            "output": float(base["output"]),
        }
        # Apply context_tiers ascending; last matching tier (total_in > threshold_tokens) wins
        ctx_tiers = sorted(
            (t for t in (rule.get("context_tiers") or ()) if isinstance(t, dict)),
            key=lambda t: int(t.get("threshold_tokens", 0)),
        )
        for tier in ctx_tiers:
            threshold = int(tier.get("threshold_tokens", 0))
            if total_in > threshold:
                for field in ("input", "input_cached", "cache_creation", "output"):
                    if tier.get(field) is not None:
                        rates[field] = float(tier[field])

        # Apply matching service_tier (case-insensitive)
        tier_query = str(service_tier or "").strip().lower()
        if tier_query:
            for st in rule.get("service_tiers") or ():
                if not isinstance(st, dict):
                    continue
                if str(st.get("match") or "").strip().lower() != tier_query:
                    continue
                for field in ("input", "input_cached", "cache_creation", "output"):
                    m_key = f"{field}_multiplier"
                    st_mult = st.get(m_key)
                    st_abs = st.get(field)
                    if st_mult is not None:
                        if rates[field] is not None and rates[field] > 0:
                            rates[field] = rates[field] * float(st_mult)
                        elif st_abs is not None:
                            rates[field] = float(st_abs)
                    elif st_abs is not None:
                        rates[field] = float(st_abs)
                break

        r_in = _price_to_microusd(float(rates["input"] or 0.0), currency=currency, multiplier=rule_mult, usd_cny_rate=usd_cny_rate)
        r_out = _price_to_microusd(float(rates["output"] or 0.0), currency=currency, multiplier=rule_mult, usd_cny_rate=usd_cny_rate)
        r_cached = (
            r_in
            if rates["input_cached"] is None
            else _price_to_microusd(float(rates["input_cached"]), currency=currency, multiplier=rule_mult, usd_cny_rate=usd_cny_rate)
        )
        r_create = (
            r_in
            if rates["cache_creation"] is None
            else _price_to_microusd(float(rates["cache_creation"]), currency=currency, multiplier=rule_mult, usd_cny_rate=usd_cny_rate)
        )
        if rule_mult == 0.0:
            return 0
        cost = (
            uncached * r_in
            + billable_cached * r_cached
            + (billable_creation + billable_cc1h) * r_create
            + out * r_out
            + 999999
        ) // 1000000
        return max(0, min(cost, MAX_MICROUSD_RATE))

    if mode == "tiered_expr":
        try:
            raw_micro, _matched_tier = evaluate_tiered_expr_rule(
                rule,
                other_input_tokens=uncached,
                cached_input_tokens=billable_cached,
                cache_creation_tokens=billable_creation,
                cache_creation_1h_tokens=billable_cc1h,
                output_tokens=out,
                service_tier=service_tier,
                model=model,
            )
        except PricingExprError:
            if strict_expr:
                raise
            return None
        microusd_f = raw_micro if currency == "USD" else (raw_micro / usd_cny_rate)
        return max(0, min(int(round(microusd_f)), MAX_MICROUSD_RATE))

    return None


def pricing_summary(settings: dict[str, Any] | None, *, now: datetime | None = None) -> dict[str, Any]:
    """Compact pricing & FX metadata embedded in maintenance diagnostics."""
    cfg = settings if isinstance(settings, dict) else default_pricing_settings()
    fx = cfg.get("exchange_rate") or {}
    rules = cfg.get("pricing_rules") or cfg.get("provider_tariffs") or {}
    schedules = cfg.get("pricing_schedules") or {}
    multipliers = cfg.get("pricing_multipliers") or {}
    return {
        "version": 2,
        "display_currency": cfg.get("display_currency", DEFAULT_DISPLAY_CURRENCY),
        "usd_cny_rate": effective_usd_cny_rate(cfg),
        "rate_mode": fx.get("mode", "auto"),
        "rate_source": effective_rate_source(cfg),
        "rate_updated_at": fx.get("fetched_at"),
        "rate_stale": is_rate_stale(cfg, now=now),
        "use_builtin_presets": bool(cfg.get("use_builtin_presets", True)),
        "configured_provider_count": len(rules),
        "active_schedule_count": sum(1 for s in schedules.values() if schedule_is_active(s)),
        "custom_multiplier_count": len(multipliers),
        "pricing_timezone": cfg.get("pricing_timezone", DEFAULT_PRICING_TIMEZONE),
        "revision": cfg.get("revision", "initial"),
    }


class PricingSettingsStore:
    """Atomic JSON store for Pricing v2 settings at ``<runtime>/pricing/settings.json``."""

    def __init__(self, runtime_root: Path):
        self.root = Path(runtime_root).resolve()
        self.directory = self.root / "pricing"
        self.path = self.directory / "settings.json"
        self._cache_stat: tuple[int, int, int] | None = None
        self._cache_value: dict[str, Any] | None = None

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return default_pricing_settings()
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_SETTINGS_BYTES:
                raise ValueError("pricing settings file is too large")
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise PricingSettingsError("pricing_config_unreadable", "模型计价配置文件无法读取") from error
        return _validate_document(data)

    def read(self) -> dict[str, Any]:
        with _SETTINGS_LOCK:
            return self._read()

    def read_safe(self) -> dict[str, Any]:
        """Read with stat caching; degrade to defaults on corrupted file without crashing chats."""
        with _SETTINGS_LOCK:
            try:
                st = self.path.stat()
            except OSError:
                self._cache_stat = None
                self._cache_value = None
                return default_pricing_settings()
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
            if self._cache_value is not None and self._cache_stat == sig:
                return deepcopy(self._cache_value)
            try:
                value = self._read()
            except PricingSettingsError:
                return default_pricing_settings()
            self._cache_stat = sig
            self._cache_value = deepcopy(value)
            return value

    def update(self, revision: object, changes: object) -> dict[str, Any]:
        with _SETTINGS_LOCK:
            current = self._read()
            if type(revision) is not str or revision != current["revision"]:
                raise PricingSettingsError("pricing_config_conflict", "模型计价配置已变化，请刷新后再保存")
            if type(changes) is not dict or not changes:
                raise PricingSettingsError("pricing_config_invalid", "请提供至少一项计价配置改动")
            allowed_keys = {
                "display_currency",
                "use_builtin_presets",
                "exchange_rate",
                "provider_tariffs",
                "pricing_rules",
                "pricing_multipliers",
                "pricing_schedules",
                "pricing_timezone",
                "price_sources",
                "price_selections",
                "price_sync",
            }
            if set(changes) - allowed_keys:
                raise PricingSettingsError("pricing_config_invalid", "模型计价配置包含不支持的字段")

            display_currency = changes.get("display_currency", current["display_currency"])
            use_builtin_presets = changes.get("use_builtin_presets", current["use_builtin_presets"])
            pricing_timezone = changes.get("pricing_timezone", current["pricing_timezone"])

            if "exchange_rate" in changes:
                fx_patch = changes["exchange_rate"]
                if type(fx_patch) is not dict:
                    raise PricingSettingsError("pricing_config_invalid", "汇率配置必须是对象")
                user_fx_allowed = {"mode", "manual_usd_cny", "fallback_usd_cny"}
                if set(fx_patch) - user_fx_allowed:
                    raise PricingSettingsError("pricing_config_invalid", "汇率配置包含只读或不支持的字段")
                merged_fx = {**current["exchange_rate"], **fx_patch}
                exchange_rate = _validate_exchange_rate(merged_fx, current["exchange_rate"])
            else:
                exchange_rate = dict(current["exchange_rate"])

            # Handle v1 provider_tariffs and v2 pricing_rules seamlessly
            if "pricing_rules" in changes:
                raw_rules = changes["pricing_rules"]
                if type(raw_rules) is not dict or len(raw_rules) > MAX_PRICING_RULES:
                    raise PricingSettingsError("pricing_config_invalid", f"计价规则不能超过 {MAX_PRICING_RULES} 条")
                pricing_rules = {
                    _validate_rule_key(k): validate_pricing_rule(v)
                    for k, v in raw_rules.items()
                }
                if "provider_tariffs" in changes:
                    raw_tariffs = changes["provider_tariffs"]
                    if type(raw_tariffs) is not dict or len(raw_tariffs) > MAX_PROVIDER_TARIFFS:
                        raise PricingSettingsError("pricing_config_invalid", f"单价规则不能超过 {MAX_PROVIDER_TARIFFS} 条")
                    provider_tariffs = dict(_validate_provider_tariff(pid, r) for pid, r in raw_tariffs.items())
                else:
                    # Keep v1 mirror in sync for provider-level per_token rules
                    provider_tariffs = {}
                    for k, v2r in pricing_rules.items():
                        if "|" not in k and len(provider_tariffs) < MAX_PROVIDER_TARIFFS:
                            mirror = _v2_rule_to_v1_mirror(v2r)
                            if mirror is not None:
                                provider_tariffs[k] = mirror
            elif "provider_tariffs" in changes:
                raw_tariffs = changes["provider_tariffs"]
                if type(raw_tariffs) is not dict or len(raw_tariffs) > MAX_PROVIDER_TARIFFS:
                    raise PricingSettingsError("pricing_config_invalid", f"单价规则不能超过 {MAX_PROVIDER_TARIFFS} 条")
                provider_tariffs = dict(_validate_provider_tariff(pid, r) for pid, r in raw_tariffs.items())
                # Preserve exact model rules ("pid|model") and update provider-level rules from v1 payload
                pricing_rules = {
                    k: deepcopy(v)
                    for k, v in current["pricing_rules"].items()
                    if "|" in k
                }
                for pid, v1_rule in provider_tariffs.items():
                    pricing_rules[pid] = _v1_tariff_to_v2_rule(v1_rule)
            else:
                provider_tariffs = deepcopy(current["provider_tariffs"])
                pricing_rules = deepcopy(current["pricing_rules"])

            pricing_multipliers = (
                changes["pricing_multipliers"]
                if "pricing_multipliers" in changes
                else deepcopy(current["pricing_multipliers"])
            )
            pricing_schedules = (
                changes["pricing_schedules"]
                if "pricing_schedules" in changes
                else deepcopy(current["pricing_schedules"])
            )
            price_sources = (
                changes["price_sources"]
                if "price_sources" in changes
                else deepcopy(current["price_sources"])
            )
            price_selections = (
                changes["price_selections"]
                if "price_selections" in changes
                else deepcopy(current["price_selections"])
            )
            if "price_sync" in changes:
                sync_patch = changes["price_sync"]
                if type(sync_patch) is not dict or (set(sync_patch) - {"auto_daily"}):
                    raise PricingSettingsError("pricing_config_invalid", "价格同步配置仅允许修改 auto_daily")
                price_sync = {**current["price_sync"], **sync_patch}
            else:
                price_sync = dict(current["price_sync"])

            candidate = _validate_document({
                "version": 2,
                "display_currency": display_currency,
                "use_builtin_presets": use_builtin_presets,
                "exchange_rate": exchange_rate,
                "provider_tariffs": provider_tariffs,
                "pricing_rules": pricing_rules,
                "pricing_multipliers": pricing_multipliers,
                "pricing_schedules": pricing_schedules,
                "pricing_timezone": pricing_timezone,
                "price_sources": price_sources,
                "price_selections": price_selections,
                "price_sync": price_sync,
                "revision": uuid4().hex,
            })
            self._write(candidate)
            self._invalidate_cache()
            return self._read()

    def mutate_internal(self, mutator, *, revision: str | None = None, bump_revision: bool = True) -> dict[str, Any]:
        """Atomically apply a server-side mutation with optional optimistic revision check."""
        with _SETTINGS_LOCK:
            current = self._read()
            if revision is not None and revision != current["revision"]:
                raise PricingSettingsError("pricing_config_conflict", "模型计价配置已变化，请刷新后再试")
            updated = deepcopy(current)
            mutator(updated)
            if bump_revision:
                updated["revision"] = uuid4().hex
            validated = _validate_document(updated)
            self._write(validated)
            self._invalidate_cache()
            return self._read()

    def record_fetched_rate(self, usd_cny: float, *, source: str, fetched_at: str) -> dict[str, Any]:
        """Persist a freshly fetched auto USD/CNY rate without invalidating user edit revisions."""
        rate = round(
            _finite_number(
                usd_cny,
                minimum=MIN_USD_CNY,
                maximum=MAX_USD_CNY,
                code="pricing_rate_invalid",
                reason="获取到的汇率超出合理区间",
            ),
            6,
        )
        if type(source) is not str or not source or _RATE_SOURCE_PATTERN.fullmatch(source) is None:
            raise PricingSettingsError("pricing_rate_invalid", "汇率来源标识无效")
        _validate_iso_timestamp(fetched_at, code="pricing_rate_invalid", label="汇率更新时间")

        def _apply(doc: dict[str, Any]) -> None:
            doc["exchange_rate"]["cached_usd_cny"] = rate
            doc["exchange_rate"]["fetched_at"] = fetched_at
            doc["exchange_rate"]["source"] = source

        return self.mutate_internal(_apply, bump_revision=False)

    def record_auto_sync_at(self, synced_at: str) -> dict[str, Any]:
        """Record background catalog daily-sync timestamp without bumping user edit revision."""
        _validate_iso_timestamp(synced_at, code="pricing_sync_invalid", label="自动同步时间")

        def _apply(doc: dict[str, Any]) -> None:
            doc["price_sync"]["last_auto_sync_at"] = synced_at

        return self.mutate_internal(_apply, bump_revision=False)

    def set_source_enabled(
        self,
        source_id: str,
        enabled: bool,
        *,
        revision: str | None = None,
    ) -> dict[str, Any]:
        if type(source_id) is not str or not source_id.strip():
            raise PricingSettingsError("pricing_config_invalid", "价格源标识无效")
        if type(enabled) is not bool:
            raise PricingSettingsError("pricing_config_invalid", "启用开关必须是布尔值")

        def _apply(doc: dict[str, Any]) -> None:
            sources = doc["price_sources"]
            if source_id not in sources:
                raise PricingSettingsError("pricing_source_not_found", f"未找到价格源: {source_id}")
            sources[source_id]["enabled"] = enabled

        return self.mutate_internal(_apply, revision=revision, bump_revision=True)

    def upsert_newapi_source(
        self,
        source_id: str,
        *,
        url: str,
        provider_id: str,
        label: str,
        enabled: bool = True,
        currency_override: str | None = None,
        auth_mode: str | None = None,
        cluster_id: str | None = None,
    ) -> dict[str, Any]:
        def _apply(doc: dict[str, Any]) -> None:
            existing = doc["price_sources"].get(source_id)
            prev_override = existing.get("currency_override") if isinstance(existing, dict) else None
            prev_auth = existing.get("auth_mode") if isinstance(existing, dict) else None
            prev_cluster = existing.get("cluster_id") if isinstance(existing, dict) else None
            entry: dict[str, Any] = {
                "enabled": enabled,
                "kind": "newapi",
                "label": label[:80] or source_id,
                "url": url[:512],
                "provider_id": provider_id[:MAX_PROVIDER_ID_CHARS],
            }
            eff_override = prev_override if currency_override is None else currency_override
            if eff_override in ("CNY", "USD"):
                entry["currency_override"] = eff_override
            eff_auth = prev_auth if auth_mode is None else auth_mode
            if eff_auth in ("anonymous", "bearer"):
                entry["auth_mode"] = eff_auth
            eff_cluster = prev_cluster if cluster_id is None else cluster_id
            if type(eff_cluster) is str and eff_cluster.strip():
                entry["cluster_id"] = eff_cluster.strip()[:MAX_PROVIDER_ID_CHARS]
            doc["price_sources"][source_id] = entry

        return self.mutate_internal(_apply, bump_revision=True)

    def set_source_currency_override(
        self,
        source_id: str,
        currency_override: str,
        *,
        fiat_per_base_unit: object = ...,
        revision: str | None = None,
    ) -> dict[str, Any]:
        if type(source_id) is not str or not source_id.strip().startswith("newapi:"):
            raise PricingSettingsError("pricing_source_invalid", "仅支持为 New API 价格源设置结算币种")
        if type(currency_override) is not str or currency_override not in ("auto", "CNY", "USD"):
            raise PricingSettingsError("pricing_source_invalid", "价格源币种覆盖仅支持 auto、CNY 或 USD")
        update_fiat_rate = fiat_per_base_unit is not ...
        parsed_fiat_rate: float | None = None
        if update_fiat_rate and fiat_per_base_unit is not None and fiat_per_base_unit != "":
            parsed_fiat_rate = round(
                _finite_number(
                    fiat_per_base_unit,
                    minimum=1e-6,
                    maximum=100_000.0,
                    code="pricing_source_invalid",
                    reason="法币折算单价必须是大于 0 且不超过 100000 的有效数字",
                ),
                6,
            )

        def _apply(doc: dict[str, Any]) -> None:
            sources = doc["price_sources"]
            if source_id not in sources:
                raise PricingSettingsError("pricing_source_not_found", f"未找到价格源: {source_id}")
            prev_override = sources[source_id].get("currency_override") or "auto"
            if currency_override == "auto":
                sources[source_id].pop("currency_override", None)
            else:
                sources[source_id]["currency_override"] = currency_override
            if update_fiat_rate:
                if parsed_fiat_rate is None:
                    sources[source_id].pop("fiat_per_base_unit", None)
                else:
                    sources[source_id]["fiat_per_base_unit"] = parsed_fiat_rate
            elif currency_override != prev_override:
                # Clear stale cross-currency conversion override whenever currency_override changes.
                sources[source_id].pop("fiat_per_base_unit", None)

        return self.mutate_internal(_apply, revision=revision, bump_revision=True)

    def set_price_selection(
        self,
        provider_id: str,
        model: str,
        price_key: str,
        *,
        revision: str | None = None,
    ) -> dict[str, Any]:
        pid = _validate_rule_key(provider_id, label="Provider 标识")
        model_clean = str(model or "").strip()
        if not model_clean or len(model_clean) > MAX_PROVIDER_ID_CHARS:
            raise PricingSettingsError("pricing_config_invalid", "模型标识无效")
        key_clean = str(price_key or "").strip()
        if not key_clean or len(key_clean) > 256:
            raise PricingSettingsError("pricing_config_invalid", "price_key 无效")

        def _apply(doc: dict[str, Any]) -> None:
            doc["price_selections"].setdefault(pid, {})[model_clean] = key_clean

        return self.mutate_internal(_apply, revision=revision, bump_revision=True)

    def clear_price_selection(
        self,
        provider_id: str,
        model: str | None = None,
        *,
        revision: str | None = None,
    ) -> dict[str, Any]:
        pid = _validate_rule_key(provider_id, label="Provider 标识")

        def _apply(doc: dict[str, Any]) -> None:
            selections = doc["price_selections"]
            if pid not in selections:
                return
            if model is None or not str(model).strip():
                selections.pop(pid, None)
            else:
                selections[pid].pop(str(model).strip(), None)
                if not selections[pid]:
                    selections.pop(pid, None)

        return self.mutate_internal(_apply, revision=revision, bump_revision=True)

    def reset_pricing(
        self,
        *,
        scope: str = "all",
        provider_id: str | None = None,
        revision: str | None = None,
    ) -> dict[str, Any]:
        valid_scopes = {"all", "rules", "schedules", "multipliers", "selections"}
        if scope not in valid_scopes:
            raise PricingSettingsError("pricing_config_invalid", "无效的重置范围")

        def _apply(doc: dict[str, Any]) -> None:
            pid = str(provider_id or "").strip()
            if pid:
                if scope in ("all", "rules"):
                    doc["pricing_rules"] = {
                        k: v for k, v in doc["pricing_rules"].items()
                        if k != pid and not k.startswith(pid + "|")
                    }
                    doc["provider_tariffs"].pop(pid, None)
                if scope in ("all", "schedules"):
                    doc["pricing_schedules"] = {
                        k: v for k, v in doc["pricing_schedules"].items()
                        if k != pid and not k.startswith(pid + "|")
                    }
                if scope in ("all", "multipliers"):
                    doc["pricing_multipliers"].pop(pid, None)
                if scope in ("all", "selections"):
                    doc["price_selections"].pop(pid, None)
            else:
                if scope in ("all", "rules"):
                    doc["pricing_rules"] = {}
                    doc["provider_tariffs"] = {}
                if scope in ("all", "schedules"):
                    doc["pricing_schedules"] = {}
                if scope in ("all", "multipliers"):
                    doc["pricing_multipliers"] = {}
                if scope in ("all", "selections"):
                    doc["price_selections"] = {}

        return self.mutate_internal(_apply, revision=revision, bump_revision=True)

    def clear_residue(
        self,
        *,
        configured_provider_ids: set[str],
        configured_cluster_ids: set[str],
        target_provider_ids: set[str] | None = None,
        revision: str | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        """Remove orphaned overrides/schedules/selections/multipliers for deleted providers."""
        cleared: set[str] = set()

        def _should_clear(pid: str) -> bool:
            if pid in configured_provider_ids:
                return False
            if target_provider_ids is not None and pid not in target_provider_ids:
                return False
            return True

        def _apply(doc: dict[str, Any]) -> None:
            new_rules: dict[str, Any] = {}
            for key, val in doc["pricing_rules"].items():
                pid = key.split("|", 1)[0]
                if _should_clear(pid):
                    cleared.add(pid)
                else:
                    new_rules[key] = val
            doc["pricing_rules"] = new_rules

            new_tariffs: dict[str, Any] = {}
            for pid, val in doc["provider_tariffs"].items():
                if _should_clear(pid):
                    cleared.add(pid)
                else:
                    new_tariffs[pid] = val
            doc["provider_tariffs"] = new_tariffs

            new_scheds: dict[str, Any] = {}
            for key, val in doc["pricing_schedules"].items():
                pid = key.split("|", 1)[0]
                if _should_clear(pid):
                    cleared.add(pid)
                else:
                    new_scheds[key] = val
            doc["pricing_schedules"] = new_scheds

            new_sels: dict[str, Any] = {}
            for pid, val in doc["price_selections"].items():
                if _should_clear(pid):
                    cleared.add(pid)
                else:
                    new_sels[pid] = val
            doc["price_selections"] = new_sels

            new_mults: dict[str, float] = {}
            for cid, val in doc["pricing_multipliers"].items():
                if cid in configured_cluster_ids or cid in configured_provider_ids:
                    new_mults[cid] = val
                elif target_provider_ids is not None and cid not in target_provider_ids:
                    new_mults[cid] = val
                else:
                    cleared.add(cid)
            doc["pricing_multipliers"] = new_mults

        updated = self.mutate_internal(_apply, revision=revision, bump_revision=True)
        return updated, sorted(cleared)

    def _invalidate_cache(self) -> None:
        self._cache_stat = None
        self._cache_value = None

    def _write(self, values: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(values, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=str(self.directory), prefix=".settings.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_path, self.path)
        finally:
            tmp_path.unlink(missing_ok=True)


def tariff_resolver_for(runtime_root: Path):
    """Build a lightweight callback that dynamically resolves a provider's active tariff."""
    settings_store = PricingSettingsStore(runtime_root)
    catalog_store = PriceCatalogStore(runtime_root)

    def resolve(
        provider_id: str,
        model: str = "",
        legacy_tariff: UsageTariff | None = None,
        *,
        timestamp: object = None,
        cluster_id: str | None = None,
    ) -> UsageTariff:
        settings = settings_store.read_safe()
        catalog = catalog_store.read_safe()
        resolved = resolve_tariff(
            provider_id,
            model,
            settings,
            legacy_tariff=legacy_tariff,
            catalog=catalog,
            cluster_id=cluster_id,
            timestamp=timestamp,
        )
        return resolved.tariff if resolved is not None else UsageTariff.unconfigured()

    return resolve
