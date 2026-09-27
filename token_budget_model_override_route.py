# -*- coding: utf-8 -*-
"""TokenBudgetModelOverrideRouteMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（21 个方法 + 0 个模块级名字 + 0 个类级赋值 / 368 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import time
from .constants import (
    MODEL_PROVIDER_KEYS,
    MODEL_QUICK_TIMEOUT_KEYS,
    MODEL_TASK_PROVIDER_KEYS,
    MODEL_TASK_PROVIDER_PREFIXES,
)
from .helpers import _flat_get, _safe_float, _single_line
from .token_budget_shared import logger
from typing import Any



class TokenBudgetModelOverrideRouteMixin:
    """TokenBudgetModelOverrideRouteMixin（从 TokenBudgetMixin 拆出）。"""


    @staticmethod
    def _normalize_request_max_attempts(value: Any) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return 0
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0

    @classmethod
    def _normalize_model_request_max_attempts_overrides(cls, value: Any) -> dict[str, int]:
        if isinstance(value, str):
            try:
                value = json.loads(value or "{}")
            except (TypeError, ValueError):
                return {}
        if not isinstance(value, dict):
            return {}
        return {
            key: attempts for key, raw in value.items()
            if key in MODEL_PROVIDER_KEYS and (attempts := cls._normalize_request_max_attempts(raw))
        }

    def _background_llm_request_policy(
        self, *, task: str = "", provider_id: str = "", provider_key: str = ""
    ) -> dict[str, Any]:
        key = self._model_provider_key_for_call(task, provider_id, provider_key)
        overrides = getattr(self, "model_request_max_attempts_overrides", {})
        attempts = self._normalize_request_max_attempts(overrides.get(key)) if isinstance(overrides, dict) else 0
        source = "model_card"
        if not attempts:
            attempts = self._normalize_request_max_attempts(
                getattr(self, "background_llm_request_max_attempts", 0)
            )
            source = "plugin"
        if not attempts:
            getter = getattr(self.context, "get_config", None)
            try:
                config = getter() if callable(getter) else {}
            except Exception:
                config = {}
            settings = config.get("provider_settings", {}) if isinstance(config, dict) else {}
            attempts = self._normalize_request_max_attempts(settings.get("request_max_retries")) if isinstance(settings, dict) else 0
            source = "astrbot_global"
        if not attempts:
            # Match AstrBot's default only when no valid configured value exists.
            try:
                from astrbot.core.provider.sources.request_retry import REQUEST_RETRY_ATTEMPTS
            except ImportError:
                REQUEST_RETRY_ATTEMPTS = 5
            attempts = REQUEST_RETRY_ATTEMPTS
            source = "astrbot_default"
        return {"request_max_attempts": attempts, "request_retry_source": source}

    @staticmethod
    def _llm_retry_kwargs(call: Any, policy: dict[str, Any], *, explicit: bool = False) -> dict[str, int]:
        try:
            params = inspect.signature(call).parameters
            supported = "request_max_retries" in params or (
                not explicit and any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            )
        except (TypeError, ValueError):
            supported = False
        policy["request_retry_supported"] = supported
        return {"request_max_retries": policy["request_max_attempts"]} if supported else {}

    def _llm_context_retry_kwargs(self, provider_id: str, policy: dict[str, Any]) -> dict[str, int]:
        getter = getattr(self.context, "get_provider_by_id", None)
        provider = getter(provider_id) if callable(getter) else None
        if provider is not None:
            # Old providers may forward arbitrary kwargs directly to the SDK.
            if not self._llm_retry_kwargs(getattr(provider, "text_chat", None), policy, explicit=True):
                return {}
        return self._llm_retry_kwargs(self.context.llm_generate, policy)

    def _llm_backoff_key(self, task: str, provider_id: str, prompt: str) -> str:
        persona_getter = getattr(self, "_active_persona_scope", None)
        persona = persona_getter() if callable(persona_getter) else ""
        return hashlib.sha256(json.dumps([persona, task, provider_id, prompt], ensure_ascii=False).encode("utf-8")).hexdigest()

    def _llm_request_retry_after(self, key: str, *, defer: bool = False) -> float:
        now = time.time()
        pending = getattr(self, "_background_llm_retry_after", {})
        pending = {k: ts for k, ts in pending.items() if ts > now}
        if defer:
            pending[key] = now + 60
        self._background_llm_retry_after = pending
        return pending.get(key, 0.0)

    @staticmethod
    def _llm_result_unknown_timeout(error: BaseException) -> bool:
        seen: set[int] = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, TimeoutError) or type(error).__name__ in {"ReadTimeout", "ReadTimeoutError", "APITimeoutError"}:
                return True
            error = error.__cause__ or error.__context__
        return False

    @staticmethod
    def _normalize_model_timeout_overrides(value: Any) -> dict[str, int]:
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, int] = {}
        for raw_key, raw_timeout in raw.items():
            key = str(raw_key or "").strip()
            if key not in MODEL_PROVIDER_KEYS:
                continue
            try:
                timeout = int(float(raw_timeout))
            except (TypeError, ValueError):
                continue
            if 5 <= timeout <= 600:
                normalized[key] = timeout
        return normalized

    @classmethod
    def _normalize_model_token_limit_overrides(cls, value: Any) -> dict[str, int]:
        """Normalize optional per-card single-request token ceilings."""
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, int] = {}
        for raw_key, raw_limit in raw.items():
            key = str(raw_key or "").strip()
            if key not in MODEL_PROVIDER_KEYS:
                continue
            try:
                limit = int(float(raw_limit))
            except (TypeError, ValueError):
                continue
            if cls.MODEL_TOKEN_LIMIT_MIN <= limit <= cls.MODEL_TOKEN_LIMIT_MAX:
                normalized[key] = limit
        return normalized

    @staticmethod
    def _normalize_model_fallback_overrides(value: Any) -> dict[str, str]:
        raw = value
        if isinstance(raw, str):
            try:
                raw = json.loads(raw or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw = {}
        if not isinstance(raw, dict):
            return {}
        normalized: dict[str, str] = {}
        for raw_key, raw_provider_id in raw.items():
            key = str(raw_key or "").strip()
            provider_id = _single_line(raw_provider_id, 160)
            if key in MODEL_PROVIDER_KEYS and provider_id:
                normalized[key] = provider_id
        return normalized

    def _model_provider_key_for_call(self, task: str, provider_id: str = "", explicit_key: str = "") -> str:
        key = str(explicit_key or "").strip()
        if key in MODEL_PROVIDER_KEYS:
            provider_key = key
        else:
            task_key = str(task or "").strip()
            provider_key = MODEL_TASK_PROVIDER_KEYS.get(task_key, "")
            if not provider_key:
                for prefix, candidate_key in MODEL_TASK_PROVIDER_PREFIXES:
                    if task_key.startswith(prefix):
                        provider_key = candidate_key
                        break
        mode = str(getattr(self, "provider_config_mode", "quick") or "quick")
        if provider_key and mode == "quick":
            provider_key = MODEL_QUICK_TIMEOUT_KEYS.get(provider_key, provider_key)
        if provider_key:
            return provider_key

        selected_provider = str(provider_id or "").strip()
        config = getattr(self, "config", {})
        if not selected_provider:
            return ""
        matching_keys = [
            candidate_key
            for candidate_key in MODEL_PROVIDER_KEYS
            if str(_flat_get(config, candidate_key, "") or "").strip() == selected_provider
        ]
        if mode == "quick":
            for candidate_key in (
                "FAST_RESPONSE_PROVIDER_ID",
                "COMPLEX_REASONING_PROVIDER_ID",
                "CREATIVE_MODEL_PROVIDER_ID",
                "PLUGIN_VISION_PROVIDER_ID",
            ):
                if candidate_key in matching_keys:
                    return candidate_key
        return matching_keys[0] if len(matching_keys) == 1 else ""

    def _model_fallback_provider_id(self, provider_key: str, primary_provider_id: str = "") -> str:
        key = str(provider_key or "").strip()
        fallbacks = getattr(self, "model_fallback_overrides", {})
        if key not in MODEL_PROVIDER_KEYS or not isinstance(fallbacks, dict):
            return ""
        fallback_id = _single_line(fallbacks.get(key), 160)
        return fallback_id if fallback_id and fallback_id != str(primary_provider_id or "").strip() else ""

    def _model_fallback_provider_for_call(
        self,
        *,
        task: str,
        primary_provider_id: str,
        provider_key: str = "",
    ) -> tuple[str, str]:
        resolved_key = self._model_provider_key_for_call(task, primary_provider_id, provider_key)
        return resolved_key, self._model_fallback_provider_id(resolved_key, primary_provider_id)

    def _model_timeout_provider_key(self, task: str, provider_id: str = "", timeout_key: str = "") -> str:
        provider_key = self._model_provider_key_for_call(task, provider_id, timeout_key)
        overrides = getattr(self, "model_timeout_overrides", {})
        if provider_key and isinstance(overrides, dict) and provider_key in overrides:
            return provider_key
        return provider_key

    def _model_timeout_seconds_for_call(
        self,
        *,
        task: str,
        provider_id: str = "",
        timeout_key: str = "",
        timeout_seconds: float | None = None,
    ) -> float | None:
        if timeout_seconds is not None:
            explicit = _safe_float(timeout_seconds, 0.0, 0.0)
            return min(600.0, explicit) if explicit >= 5.0 else None
        overrides = getattr(self, "model_timeout_overrides", {})
        if not isinstance(overrides, dict):
            return None
        provider_key = self._model_timeout_provider_key(task, provider_id, timeout_key)
        configured = _safe_float(overrides.get(provider_key), 0.0, 0.0)
        return min(600.0, configured) if configured >= 5.0 else None

    def _model_token_limit_provider_key(
        self,
        task: str,
        provider_id: str = "",
        token_limit_key: str = "",
    ) -> str:
        return self._model_provider_key_for_call(task, provider_id, token_limit_key)

    def _model_token_limit_for_call(
        self,
        *,
        task: str,
        provider_id: str = "",
        token_limit_key: str = "",
        token_limit: int | float | None = None,
    ) -> int | None:
        """Return the configured card ceiling, or ``None`` when disabled."""
        raw = token_limit
        if raw is None:
            overrides = getattr(self, "model_token_limit_overrides", {})
            if not isinstance(overrides, dict):
                return None
            provider_key = self._model_token_limit_provider_key(task, provider_id, token_limit_key)
            raw = overrides.get(provider_key)
        try:
            parsed = int(float(raw))
        except (TypeError, ValueError):
            return None
        if self.MODEL_TOKEN_LIMIT_MIN <= parsed <= self.MODEL_TOKEN_LIMIT_MAX:
            return parsed
        return None

    @classmethod
    def _estimate_model_request_tokens(
        cls,
        prompt: Any,
        *,
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
    ) -> int:
        """Estimate text, bounded image input, and requested output tokens."""
        parts = [
            str(system_prompt or "").strip(),
            str(prompt or "").strip(),
            str(tool_schema or "").strip(),
        ]
        input_text = "\n\n".join(part for part in parts if part)
        try:
            output_tokens = max(0, int(float(max_tokens or 0)))
        except (TypeError, ValueError):
            output_tokens = 0
        try:
            images = max(0, int(float(image_count or 0)))
        except (TypeError, ValueError):
            images = 0
        return (
            cls._estimate_token_count(input_text)
            + output_tokens
            + images * cls.MODEL_IMAGE_TOKEN_ESTIMATE
        )

    def _model_token_limit_route_for_call(
        self,
        *,
        task: str,
        primary_provider_id: str,
        fallback_provider_id: str,
        provider_key: str = "",
        prompt: Any = "",
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
        token_limit: int | float | None = None,
    ) -> tuple[bool, int | None, int]:
        """Decide whether a card's fallback should handle this request."""
        if not fallback_provider_id or not primary_provider_id:
            return False, None, self._estimate_model_request_tokens(
                prompt,
                system_prompt=system_prompt,
                tool_schema=tool_schema,
                max_tokens=max_tokens,
                image_count=image_count,
            )
        limit = self._model_token_limit_for_call(
            task=task,
            provider_id=primary_provider_id,
            token_limit_key=provider_key,
            token_limit=token_limit,
        )
        estimate = self._estimate_model_request_tokens(
            prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            image_count=image_count,
        )
        if limit is None or estimate <= limit:
            return False, limit, estimate
        logger.warning(
            "请求预估 Token 超过模型卡上限，跳过主模型并切换备用模型: task=%s card=%s estimate=%s limit=%s primary=%s fallback=%s",
            _single_line(task, 80) or "unknown",
            provider_key or "unknown",
            estimate,
            limit,
            _single_line(primary_provider_id, 120),
            _single_line(fallback_provider_id, 120),
        )
        return True, limit, estimate

    def _model_token_limit_should_skip_primary(
        self,
        *,
        task: str,
        provider_id: str,
        primary_provider_id: str,
        fallback_provider_id: str,
        provider_key: str,
        prompt: Any = "",
        system_prompt: Any = "",
        tool_schema: Any = "",
        max_tokens: Any = 0,
        image_count: Any = 0,
        token_limit: int | float | None = None,
    ) -> bool:
        """Return whether a direct provider path should skip its primary card."""
        if not provider_id or provider_id != primary_provider_id:
            return False
        routed, _limit, _estimate = self._model_token_limit_route_for_call(
            task=task,
            primary_provider_id=primary_provider_id,
            fallback_provider_id=fallback_provider_id,
            provider_key=provider_key,
            prompt=prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            image_count=image_count,
            token_limit=token_limit,
        )
        return routed
