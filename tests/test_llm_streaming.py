# -*- coding: utf-8 -*-
"""Streaming accumulation for plugin-internal LLM calls (enable_llm_streaming)."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any

from astrbot_plugin_private_companion.token_budget import TokenBudgetMixin


class _StreamProvider:
    def __init__(self, chunks: list[Any] | None = None, final: Any | None = None) -> None:
        self._chunks = list(chunks or [])
        self._final = final

    async def text_chat_stream(self, **kwargs: Any) -> Any:
        for chunk in self._chunks:
            yield chunk
        if self._final is not None:
            yield self._final


class _ProviderManager:
    def __init__(self, provider: Any | None = None) -> None:
        self._provider = provider

    async def get_provider_by_id(self, provider_id: str) -> Any | None:
        return self._provider


class _Context:
    def __init__(self, provider_manager: Any) -> None:
        self.provider_manager = provider_manager


class _Owner(TokenBudgetMixin):
    def __init__(self, context: Any, streaming: bool = False) -> None:
        self.context = context
        self.enable_llm_streaming = streaming


def _chunk(text: str) -> Any:
    return SimpleNamespace(is_chunk=True, completion_text=text)


def _final(text: str) -> Any:
    return SimpleNamespace(is_chunk=False, completion_text=text)


class TestLlmStreamingGate(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        owner = _Owner(_Context(_ProviderManager()), streaming=False)
        self.assertFalse(
            TokenBudgetMixin._llm_streaming_enabled_for_call(owner, task="creative_writing", max_tokens=1200)
        )

    def test_enabled_for_large_tasks(self) -> None:
        owner = _Owner(_Context(_ProviderManager()), streaming=True)
        self.assertTrue(
            TokenBudgetMixin._llm_streaming_enabled_for_call(owner, task="creative_writing", max_tokens=1200)
        )

    def test_enabled_even_without_max_tokens(self) -> None:
        owner = _Owner(_Context(_ProviderManager()), streaming=True)
        self.assertTrue(
            TokenBudgetMixin._llm_streaming_enabled_for_call(owner, task="creative_writing", max_tokens=0)
        )

    def test_short_tasks_stay_non_streaming(self) -> None:
        owner = _Owner(_Context(_ProviderManager()), streaming=True)
        threshold = TokenBudgetMixin.MODEL_TOKEN_LIMIT_MIN * 2
        self.assertFalse(
            TokenBudgetMixin._llm_streaming_enabled_for_call(owner, task="creative_writing", max_tokens=threshold - 1)
        )
        self.assertTrue(
            TokenBudgetMixin._llm_streaming_enabled_for_call(owner, task="creative_writing", max_tokens=threshold)
        )


class TestLlmStreamingAccumulation(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_only_provider_accumulates(self) -> None:
        provider = _StreamProvider(chunks=[_chunk("第一段"), _chunk("第二段"), _chunk("第三段")])
        owner = _Owner(_Context(_ProviderManager(provider)))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="星渊/deepseek-v4-pro-0813",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNotNone(resp)
        self.assertEqual(resp.completion_text, "第一段第二段第三段")

    async def test_final_response_wins_over_chunks(self) -> None:
        provider = _StreamProvider(
            chunks=[_chunk("增量一"), _chunk("增量二")],
            final=_final("完整结果正文"),
        )
        owner = _Owner(_Context(_ProviderManager(provider)))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="p",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNotNone(resp)
        # The complete final response must not be duplicated with the deltas.
        self.assertEqual(resp.completion_text, "完整结果正文")

    async def test_empty_final_response_uses_accumulated_chunks(self) -> None:
        provider = _StreamProvider(
            chunks=[_chunk("增量内容")],
            final=_final(""),
        )
        owner = _Owner(_Context(_ProviderManager(provider)))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="p",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNotNone(resp)
        self.assertEqual(resp.completion_text, "增量内容")

    async def test_empty_stream_returns_none(self) -> None:
        provider = _StreamProvider()
        owner = _Owner(_Context(_ProviderManager(provider)))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="p",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNone(resp)

    async def test_missing_provider_returns_none(self) -> None:
        owner = _Owner(_Context(_ProviderManager(None)))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="missing",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNone(resp)

    async def test_provider_without_stream_support_returns_none(self) -> None:
        class _NoStreamProvider:
            pass

        owner = _Owner(_Context(_ProviderManager(_NoStreamProvider())))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="p",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNone(resp)

    async def test_stream_error_returns_none_for_non_streaming_fallback(self) -> None:
        class _BrokenProvider:
            async def text_chat_stream(self, **kwargs: Any) -> Any:
                raise NotImplementedError("stream unsupported")
                yield  # pragma: no cover

        owner = _Owner(_Context(_ProviderManager(_BrokenProvider())))
        resp = await TokenBudgetMixin._llm_generate_streaming(
            owner,
            provider_id="p",
            prompt="续写",
            max_tokens=1000,
        )
        self.assertIsNone(resp)

    async def test_timeout_applies_to_stream(self) -> None:
        import asyncio

        class _SlowProvider:
            async def text_chat_stream(self, **kwargs: Any) -> Any:
                await asyncio.sleep(0.2)
                yield _chunk("迟到内容")

        owner = _Owner(_Context(_ProviderManager(_SlowProvider())))
        with self.assertRaises(asyncio.TimeoutError):
            await TokenBudgetMixin._llm_generate_streaming(
                owner,
                provider_id="p",
                prompt="续写",
                max_tokens=1000,
                timeout_seconds=0.05,
            )


class _NoStreamProvider:
    pass


class _LlmCallContext:
    """Fake context: streaming provider + counting ``llm_generate``."""

    def __init__(self, provider: Any | None = None) -> None:
        self.provider_manager = _ProviderManager(provider)
        self.llm_generate_calls = 0
        self.last_kwargs: dict[str, Any] | None = None

    async def llm_generate(self, **kwargs: Any) -> Any:
        self.llm_generate_calls += 1
        self.last_kwargs = kwargs
        return _final("非流式兜底结果")


class _LlmCallHarness(TokenBudgetMixin):
    def __init__(self, context: _LlmCallContext, *, streaming: bool = True) -> None:
        self.context = context
        self.enable_llm_streaming = streaming
        self.provider_config_mode = "precision"
        self.llm_provider_id = "primary"
        self.model_timeout_overrides = {}
        self.model_fallback_overrides = {}
        self.config = {}
        self.usage: list[dict[str, Any]] = []

    @staticmethod
    def _classify_llm_prompt(_prompt: str) -> str:
        return "other"

    @staticmethod
    def _is_llm_budget_exempt_task(_task: str) -> bool:
        return False

    @staticmethod
    def _daily_token_soft_limit_should_defer(_task: str) -> bool:
        return False

    @staticmethod
    def _llm_daily_budget_remaining() -> int:
        return 100000

    def _record_llm_usage(self, **kwargs: Any) -> None:
        self.usage.append(kwargs)

    @staticmethod
    def _model_fallback_provider_for_call(**_kwargs: Any) -> tuple[str, str]:
        return ("", "")


class TestLlmCallStreamingFallback(unittest.IsolatedAsyncioTestCase):
    """End-to-end ``_llm_call`` behavior when streaming is on/off."""

    async def test_streaming_unsupported_falls_back_to_non_streaming(self) -> None:
        context = _LlmCallContext(provider=_NoStreamProvider())
        harness = _LlmCallHarness(context, streaming=True)

        text = await harness._llm_call(
            "续写一小段",
            max_tokens=1200,
            provider_id="primary",
            task="creative_writing",
        )

        self.assertEqual(text, "非流式兜底结果")
        self.assertEqual(context.llm_generate_calls, 1)
        self.assertEqual(context.last_kwargs["chat_provider_id"], "primary")

    async def test_empty_stream_falls_back_to_non_streaming(self) -> None:
        context = _LlmCallContext(provider=_StreamProvider())
        harness = _LlmCallHarness(context, streaming=True)

        text = await harness._llm_call(
            "续写一小段",
            max_tokens=1200,
            provider_id="primary",
            task="creative_writing",
        )

        self.assertEqual(text, "非流式兜底结果")
        self.assertEqual(context.llm_generate_calls, 1)

    async def test_streaming_disabled_goes_directly_to_llm_generate(self) -> None:
        context = _LlmCallContext(provider=_NoStreamProvider())
        harness = _LlmCallHarness(context, streaming=False)

        text = await harness._llm_call(
            "续写一小段",
            max_tokens=1200,
            provider_id="primary",
            task="creative_writing",
        )

        self.assertEqual(text, "非流式兜底结果")
        self.assertEqual(context.llm_generate_calls, 1)

    async def test_streaming_success_does_not_call_llm_generate(self) -> None:
        provider = _StreamProvider(chunks=[_chunk("第一段"), _chunk("第二段")])
        context = _LlmCallContext(provider=provider)
        harness = _LlmCallHarness(context, streaming=True)

        text = await harness._llm_call(
            "续写一小段",
            max_tokens=1200,
            provider_id="primary",
            task="creative_writing",
        )

        self.assertEqual(text, "第一段第二段")
        self.assertEqual(context.llm_generate_calls, 0)

    async def test_short_task_stays_non_streaming_and_uses_llm_generate(self) -> None:
        context = _LlmCallContext(provider=_StreamProvider(chunks=[_chunk("本不该被使用")]))
        harness = _LlmCallHarness(context, streaming=True)

        text = await harness._llm_call(
            "短问题",
            max_tokens=300,  # below MODEL_TOKEN_LIMIT_MIN * 2
            provider_id="primary",
            task="creative_writing",
        )

        self.assertEqual(text, "非流式兜底结果")
        self.assertEqual(context.llm_generate_calls, 1)


if __name__ == "__main__":
    unittest.main()
