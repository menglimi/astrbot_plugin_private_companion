# -*- coding: utf-8 -*-
"""TokenBudgetLlmToolMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 277 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

import asyncio
import json
import time
from .helpers import _single_line
from .token_budget_shared import _looks_like_upstream_llm_error_response, logger
from typing import Any



class TokenBudgetLlmToolMixin:
    """TokenBudgetLlmToolMixin（从 TokenBudgetMixin 拆出）。"""


    @staticmethod
    def _llm_tool_schema_for_usage(tools: Any) -> str:
        """Serialize a tool schema for fallback token estimation only."""
        if tools is None:
            return ""
        schema: Any = None
        for method_name in ("openai_schema", "get_func_desc_openai_style"):
            builder = getattr(tools, method_name, None)
            if not callable(builder):
                continue
            try:
                schema = builder()
                break
            except Exception:
                continue
        if schema is None:
            schema = tools
        try:
            return json.dumps(schema, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(schema or "")

    @staticmethod
    def _llm_tool_response_for_usage(response: Any, completion: str) -> str:
        """Include tool-only output when a provider omits native usage data."""
        if response is None:
            return completion
        names = getattr(response, "tools_call_name", None) or []
        arguments = getattr(response, "tools_call_args", None) or []
        if not names and not arguments:
            return completion
        try:
            tool_output = json.dumps(
                {"tools_call_name": names, "tools_call_args": arguments},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
        except Exception:
            tool_output = str({"tools_call_name": names, "tools_call_args": arguments})
        return "\n\n".join(part for part in (completion, tool_output) if part)

    async def _llm_tool_call(
        self,
        prompt: str,
        *,
        tools: Any,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
        system_prompt: str | None = None,
        timeout_key: str | None = None,
        timeout_seconds: float | None = None,
        token_limit: int | float | None = None,
        strict_provider: bool = False,
    ) -> Any | None:
        """Call a provider with native tools and one optional card fallback."""
        selected_provider = self._resolve_chat_provider_id(provider_id)
        # Keep the full registered task identifier for prompt-override
        # resolution.  A few persona JSON-repair keys are longer than the
        # historical 40-character logging limit.
        task_key = _single_line(task, 120) or self._classify_llm_prompt(prompt)
        prompt, system_prompt = self._apply_task_prompt_override_for_call(
            task_key,
            prompt,
            system_prompt,
        )
        tool_schema = self._llm_tool_schema_for_usage(tools)
        usage_prompt = "\n\n".join(
            part
            for part in (
                str(system_prompt or "").strip(),
                str(prompt or "").strip(),
                tool_schema,
            )
            if part
        )
        backoff_key = self._llm_backoff_key(task_key, selected_provider, usage_prompt)
        if self._llm_request_retry_after(backoff_key):
            return None
        budget_exempt = self._is_llm_budget_exempt_task(task_key)
        if not budget_exempt and self._daily_token_soft_limit_should_defer(task_key):
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=usage_prompt,
                error="daily_token_soft_limit_deferred",
            )
            return None
        if not budget_exempt and self._llm_daily_budget_remaining() == 0:
            self._record_llm_budget_skip(
                provider_id=selected_provider,
                task=task_key,
                prompt=usage_prompt,
            )
            return None
        if not selected_provider:
            return None
        if strict_provider:
            provider_key, fallback_provider = str(timeout_key or task_key), ""
        else:
            provider_key, fallback_provider = self._model_fallback_provider_for_call(
                task=task_key,
                primary_provider_id=selected_provider,
                provider_key=str(timeout_key or ""),
            )
        token_routed, _, estimated_tokens = self._model_token_limit_route_for_call(
            task=task_key,
            primary_provider_id=selected_provider,
            fallback_provider_id=fallback_provider,
            provider_key=provider_key or str(timeout_key or ""),
            prompt=prompt,
            system_prompt=system_prompt,
            tool_schema=tool_schema,
            max_tokens=max_tokens,
            token_limit=token_limit,
        )
        candidates = ([fallback_provider] if token_routed else [selected_provider])
        if not token_routed and fallback_provider:
            candidates.append(fallback_provider)
        sensitive_replacement = (
            ""
            if strict_provider
            else self._sensitive_model_replacement_provider(selected_provider)
        )
        sensitive_replacement_used = False
        for attempt_index, attempt_provider in enumerate(candidates):
            started_at = time.time()
            response = None
            request_policy = self._background_llm_request_policy(
                task=task_key, provider_id=attempt_provider, provider_key=provider_key
            )
            try:
                kwargs: dict[str, Any] = {
                    "prompt": prompt,
                    "chat_provider_id": attempt_provider,
                    "tools": tools,
                    **self._llm_context_retry_kwargs(attempt_provider, request_policy),
                }
                if max_tokens and max_tokens > 0:
                    kwargs["max_tokens"] = max_tokens
                if system_prompt:
                    kwargs["system_prompt"] = system_prompt
                effective_timeout = self._model_timeout_seconds_for_call(
                    task=task_key,
                    provider_id=attempt_provider,
                    timeout_key=provider_key or str(timeout_key or ""),
                    timeout_seconds=timeout_seconds,
                )
                request_call = self.context.llm_generate(**kwargs)
                try:
                    response = (
                        await asyncio.wait_for(request_call, timeout=effective_timeout)
                        if effective_timeout is not None
                        else await request_call
                    )
                except asyncio.TimeoutError as exc:
                    if effective_timeout is None:
                        raise TimeoutError(f"模型任务 {task_key} 调用超时") from exc
                    raise TimeoutError(f"模型任务 {task_key} 超过 {effective_timeout:.0f} 秒未返回") from exc

                completion = str(getattr(response, "completion_text", "") or "").strip()
                usage_completion = self._llm_tool_response_for_usage(response, completion)
                response_role = _single_line(getattr(response, "role", ""), 20).lower()
                semantic_provider_error = _looks_like_upstream_llm_error_response(completion)
                sensitive_keyword = (
                    ""
                    if response_role == "err" or semantic_provider_error
                    else self._sensitive_model_replacement_keyword(completion)
                )
                if sensitive_keyword:
                    if sensitive_replacement and not sensitive_replacement_used:
                        sensitive_replacement_used = True
                        candidates.append(sensitive_replacement)
                        logger.info(
                            "插件工具模型命中敏感拒答，切换指定模型重试: provider=%s target=%s keyword=%s",
                            _single_line(attempt_provider, 120),
                            _single_line(sensitive_replacement, 120),
                            _single_line(sensitive_keyword, 80),
                        )
                        continue
                    logger.warning(
                        "插件工具指定模型仍返回敏感拒答，丢弃本次文本: provider=%s keyword=%s",
                        _single_line(attempt_provider, 120),
                        _single_line(sensitive_keyword, 80),
                    )
                    return None
                if response_role == "err" or semantic_provider_error:
                    failure_code = (
                        "provider_error_role"
                        if response_role == "err"
                        else "semantic_provider_error"
                    )
                    self._record_llm_usage(
                        provider_id=attempt_provider,
                        task=task_key,
                        prompt=usage_prompt,
                        completion=usage_completion,
                        elapsed_ms=int((time.time() - started_at) * 1000),
                        success=False,
                        error=failure_code,
                        resp=response,
                        budget_exempt=budget_exempt,
                        request_policy=request_policy,
                    )
                    if attempt_index + 1 < len(candidates):
                        logger.warning(
                            "工具调用主模型失败，尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s kind=%s",
                            _single_line(task_key, 80) or "unknown",
                            provider_key or "unknown",
                            _single_line(selected_provider, 120),
                            _single_line(candidates[attempt_index + 1], 120),
                            failure_code,
                        )
                        continue
                    return None
                if response is None:
                    self._record_llm_usage(
                        provider_id=attempt_provider,
                        task=task_key,
                        prompt=usage_prompt,
                        completion="",
                        elapsed_ms=int((time.time() - started_at) * 1000),
                        success=False,
                        error="empty_response",
                        budget_exempt=budget_exempt,
                        request_policy=request_policy,
                    )
                    if attempt_index + 1 < len(candidates):
                        continue
                    return None
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion=usage_completion,
                    elapsed_ms=int((time.time() - started_at) * 1000),
                    success=True,
                    resp=response,
                    budget_exempt=budget_exempt,
                    request_policy=request_policy,
                )
                if attempt_index > 0 or token_routed:
                    logger.info(
                        "工具调用使用备用模型: task=%s card=%s provider=%s estimated_tokens=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120),
                        estimated_tokens,
                    )
                return response
            except Exception as exc:
                uncertain_timeout = (not max_tokens or max_tokens >= 512) and self._llm_result_unknown_timeout(exc)
                if uncertain_timeout:
                    request_policy["retry_after"] = self._llm_request_retry_after(backoff_key, defer=True)
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - started_at) * 1000),
                    success=False,
                    error=str(exc),
                    resp=response,
                    budget_exempt=budget_exempt,
                    request_policy=request_policy,
                )
                if uncertain_timeout:
                    raise
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "工具调用失败，尝试卡片备用模型: task=%s card=%s error=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(exc, 160),
                    )
                    continue
                raise
        return None
