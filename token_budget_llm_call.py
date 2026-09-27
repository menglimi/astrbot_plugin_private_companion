# -*- coding: utf-8 -*-
"""TokenBudgetLlmCallMixin。

由 tools/split_mixin_domain.py 从 token_budget.py 机械抽取（3 个方法 + 0 个模块级名字 + 0 个类级赋值 / 404 行）。
方法体零改动：所有 self.xxx 依赖通过继承链解析（宿主类 TokenBudgetMixin）。
"""
from __future__ import annotations

import asyncio
import time
from .helpers import _single_line
from .persona_config import runtime_persona_setting
from .token_budget_shared import _looks_like_upstream_llm_error_response, logger
from typing import Any



class TokenBudgetLlmCallMixin:
    """TokenBudgetLlmCallMixin（从 TokenBudgetMixin 拆出）。"""


    def _llm_streaming_enabled_for_call(
        self,
        *,
        task: str | None = None,
        max_tokens: int = 0,
    ) -> bool:
        """Whether this plugin-internal LLM call should use streaming.

        Streaming accumulates chunked responses instead of waiting for one
        large payload, which avoids drops/truncation on OpenAI-compatible
        relays for big outputs (creative writing, long reviews, ...).  The
        AstrBot global "streaming response" toggle does not affect
        plugin-internal calls (they always go through ``text_chat``), so this
        is an independent switch.
        """
        if not bool(runtime_persona_setting(self, "enable_llm_streaming", False)):
            return False
        # Keep very short calls non-streaming: the extra chunked round-trips
        # are pure overhead for small outputs.
        min_threshold = getattr(type(self), "MODEL_TOKEN_LIMIT_MIN", 256) * 2
        if max_tokens and 0 < max_tokens < min_threshold:
            return False
        return True

    async def _llm_generate_streaming(
        self,
        *,
        provider_id: str,
        prompt: str,
        system_prompt: str | None = None,
        max_tokens: int = 0,
        timeout_seconds: float | None = None,
        task: str | None = None,
        request_policy: dict[str, Any] | None = None,
    ) -> Any:
        """Call ``provider.text_chat_stream`` and accumulate chunks.

        AstrBot-compliant providers yield per-token chunks (``is_chunk=True``)
        and finish with one complete response (``is_chunk=False``); when a
        provider only emits chunks, the accumulated text is used instead.
        Returns ``None`` when the provider is unavailable or the stream is
        empty, so the caller's existing empty/fallback handling applies.
        """
        provider_manager = getattr(self.context, "provider_manager", None)
        getter = getattr(provider_manager, "get_provider_by_id", None)
        if not callable(getter):
            return None
        provider = await getter(provider_id)
        if provider is None:
            return None
        streamer = getattr(provider, "text_chat_stream", None)
        if not callable(streamer):
            return None

        policy = request_policy if request_policy is not None else self._background_llm_request_policy(
            task=task or "", provider_id=provider_id
        )
        stream_kwargs: dict[str, Any] = {
            "prompt": prompt,
            **self._llm_retry_kwargs(streamer, policy, explicit=True),
        }
        if system_prompt:
            stream_kwargs["system_prompt"] = system_prompt
        if max_tokens and max_tokens > 0:
            stream_kwargs["max_tokens"] = max_tokens

        chunk_parts: list[str] = []
        final_resp: Any = None
        last_chunk: Any = None

        async def _collect() -> Any:
            nonlocal final_resp, last_chunk
            async for resp in streamer(**stream_kwargs):
                if resp is None:
                    continue
                final_resp = resp
                if getattr(resp, "is_chunk", False):
                    last_chunk = resp
                    text = getattr(resp, "completion_text", "")
                    if not isinstance(text, str):
                        text = str(text or "")
                    if text:
                        chunk_parts.append(text)
            return final_resp

        try:
            if timeout_seconds is not None:
                collected = await asyncio.wait_for(_collect(), timeout=timeout_seconds)
            else:
                collected = await _collect()
        except asyncio.TimeoutError:
            raise
        except NotImplementedError as exc:
            if chunk_parts:
                raise
            logger.debug(
                "流式 Provider 调用不可用，回退非流式: provider=%s error=%s",
                _single_line(provider_id, 120),
                _single_line(exc, 160),
            )
            return None
        if collected is None:
            return None
        if not getattr(collected, "is_chunk", False):
            final_text = getattr(collected, "completion_text", "")
            if not isinstance(final_text, str):
                final_text = str(final_text or "")
            if final_text.strip():
                if getattr(collected, "usage", None) is None and last_chunk is not None:
                    chunk_usage = getattr(last_chunk, "usage", None)
                    if chunk_usage is not None:
                        try:
                            collected.usage = chunk_usage
                        except Exception:
                            pass
                return collected
            if not chunk_parts:
                return None
            try:
                collected.completion_text = "".join(chunk_parts)
            except Exception:
                return None
            return collected
        accumulated = "".join(chunk_parts)
        if not accumulated:
            return None
        try:
            collected.completion_text = accumulated
        except Exception:
            return None
        return collected

    async def _llm_call(
        self,
        prompt: str,
        max_tokens: int = 600,
        provider_id: str | None = None,
        task: str | None = None,
        *,
        system_prompt: str | None = None,
        timeout_key: str | None = None,
        timeout_seconds: float | None = None,
        token_limit: int | float | None = None,
        strict_provider: bool = False,
    ) -> str | None:
        selected_provider = self._resolve_chat_provider_id(provider_id)
        peak_router = getattr(self, "_apply_deepseek_peak_replacement", None)
        if not strict_provider and callable(peak_router) and (
            str(provider_id or "").strip()
            or str(runtime_persona_setting(self, "llm_provider_id", "") or "").strip()
        ):
            selected_provider = peak_router(selected_provider)
        # Do not truncate task identifiers before the registry resolves an
        # exact or dynamic-family override.
        task_key = _single_line(task, 120) or self._classify_llm_prompt(prompt)
        prompt, system_prompt = self._apply_task_prompt_override_for_call(
            task_key,
            prompt,
            system_prompt,
        )
        usage_prompt = (
            f"{str(system_prompt or '').strip()}\n\n{str(prompt or '').strip()}".strip()
            if str(system_prompt or "").strip()
            else str(prompt or "")
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
            self._record_llm_budget_skip(provider_id=selected_provider, task=task_key, prompt=usage_prompt)
            return None
        if strict_provider:
            provider_key, fallback_provider = str(timeout_key or task_key), ""
        else:
            provider_key, fallback_provider = self._model_fallback_provider_for_call(
                task=task_key,
                primary_provider_id=selected_provider,
                provider_key=str(timeout_key or ""),
            )
        if fallback_provider and callable(peak_router):
            fallback_provider = peak_router(fallback_provider)
        token_routed, _, estimated_tokens = self._model_token_limit_route_for_call(
            task=task_key,
            primary_provider_id=selected_provider,
            fallback_provider_id=fallback_provider,
            provider_key=provider_key or str(timeout_key or ""),
            prompt=prompt,
            system_prompt=system_prompt,
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
            start = time.time()
            resp = None
            request_policy = self._background_llm_request_policy(
                task=task_key, provider_id=attempt_provider, provider_key=provider_key
            )
            try:
                if not attempt_provider:
                    raise RuntimeError("未找到可用的 AstrBot 默认模型 Provider")
                kwargs: dict[str, Any] = {
                    "prompt": prompt, "chat_provider_id": attempt_provider,
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
                try:
                    streaming_enabled = getattr(self, "_llm_streaming_enabled_for_call", None)
                    if callable(streaming_enabled) and streaming_enabled(
                        task=task_key,
                        max_tokens=max_tokens,
                    ):
                        resp = await self._llm_generate_streaming(
                            provider_id=attempt_provider,
                            prompt=prompt,
                            system_prompt=system_prompt,
                            max_tokens=max_tokens,
                            timeout_seconds=effective_timeout,
                            task=task_key,
                            request_policy=request_policy,
                        )
                        if resp is None:
                            # 流式路径不可用（Provider 不支持流式、流式为空或
                            # 能力缺失）时回退到原有非流式调用，避免误判为空
                            # 响应而触发备用模型。
                            self._llm_context_retry_kwargs(attempt_provider, request_policy)
                            request_call = self.context.llm_generate(**kwargs)
                            if effective_timeout is not None:
                                resp = await asyncio.wait_for(request_call, timeout=effective_timeout)
                            else:
                                resp = await request_call
                    else:
                        request_call = self.context.llm_generate(**kwargs)
                        if effective_timeout is not None:
                            resp = await asyncio.wait_for(request_call, timeout=effective_timeout)
                        else:
                            resp = await request_call
                except asyncio.TimeoutError as exc:
                    if effective_timeout is None:
                        raise TimeoutError(f"模型任务 {task_key} 调用超时") from exc
                    raise TimeoutError(f"模型任务 {task_key} 超过 {effective_timeout:.0f} 秒未返回") from exc
                if resp and resp.completion_text:
                    completion = resp.completion_text.strip()
                    if completion:
                        response_role = _single_line(getattr(resp, "role", ""), 20).lower()
                        semantic_provider_error = _looks_like_upstream_llm_error_response(
                            completion
                        )
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
                                    "插件模型命中敏感拒答，切换指定模型重试: provider=%s target=%s keyword=%s",
                                    _single_line(attempt_provider, 120),
                                    _single_line(sensitive_replacement, 120),
                                    _single_line(sensitive_keyword, 80),
                                )
                                continue
                            logger.warning(
                                "插件指定模型仍返回敏感拒答，丢弃本次文本: provider=%s keyword=%s",
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
                                completion=completion,
                                elapsed_ms=int((time.time() - start) * 1000),
                                success=False,
                                error=failure_code,
                                resp=resp,
                                budget_exempt=budget_exempt,
                                request_policy=request_policy,
                            )
                            if attempt_index + 1 < len(candidates):
                                logger.warning(
                                    "主模型返回 Provider 错误响应,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s kind=%s",
                                    _single_line(task_key, 80) or "unknown",
                                    provider_key or "unknown",
                                    _single_line(attempt_provider, 120),
                                    _single_line(candidates[attempt_index + 1], 120),
                                    failure_code,
                                )
                            else:
                                logger.warning(
                                    "LLM 返回 Provider 错误响应: task=%s provider=%s kind=%s",
                                    _single_line(task_key, 80) or "unknown",
                                    _single_line(attempt_provider, 120) or "default",
                                    failure_code,
                                )
                            continue
                        self._record_llm_usage(
                            provider_id=attempt_provider,
                            task=task_key,
                            prompt=usage_prompt,
                            completion=completion,
                            elapsed_ms=int((time.time() - start) * 1000),
                            success=True,
                            resp=resp,
                            budget_exempt=budget_exempt,
                            request_policy=request_policy,
                        )
                        if attempt_index > 0 or token_routed:
                            logger.info(
                                "备用模型调用成功: task=%s card=%s provider=%s estimated_tokens=%s",
                                _single_line(task_key, 80) or "unknown",
                                provider_key or "unknown",
                                _single_line(attempt_provider, 120),
                                estimated_tokens,
                            )
                        return completion
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error="empty_response",
                    resp=resp,
                    budget_exempt=budget_exempt,
                    request_policy=request_policy,
                )
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "主模型返回空结果,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120),
                        _single_line(candidates[attempt_index + 1], 120),
                    )
            except Exception as e:
                uncertain_timeout = (not max_tokens or max_tokens >= 512) and self._llm_result_unknown_timeout(e)
                if uncertain_timeout:
                    request_policy["retry_after"] = self._llm_request_retry_after(backoff_key, defer=True)
                self._record_llm_usage(
                    provider_id=attempt_provider,
                    task=task_key,
                    prompt=usage_prompt,
                    completion="",
                    elapsed_ms=int((time.time() - start) * 1000),
                    success=False,
                    error=str(e),
                    budget_exempt=budget_exempt,
                    request_policy=request_policy,
                )
                if uncertain_timeout:
                    logger.warning("模型任务结果未知，退避后再尝试: task=%s retry_after=%s", task_key, request_policy["retry_after"])
                    return None
                if attempt_index + 1 < len(candidates):
                    logger.warning(
                        "主模型调用失败,尝试卡片备用模型: task=%s card=%s primary=%s fallback=%s error=%s",
                        _single_line(task_key, 80) or "unknown",
                        provider_key or "unknown",
                        _single_line(attempt_provider, 120) or "default",
                        _single_line(candidates[attempt_index + 1], 120),
                        _single_line(e, 160),
                    )
                    continue
                logger.warning(
                    "LLM 调用失败: task=%s provider=%s error=%s",
                    _single_line(task_key, 80) or "unknown",
                    _single_line(attempt_provider, 120) or "default",
                    _single_line(e, 160),
                )
        return None
