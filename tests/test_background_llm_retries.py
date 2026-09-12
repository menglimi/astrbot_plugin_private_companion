# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_private_companion.token_budget import TokenBudgetMixin
from .test_llm_streaming import _LlmCallContext, _LlmCallHarness as _BaseHarness, _final


class _LlmCallHarness(_BaseHarness):
    _model_fallback_provider_for_call = TokenBudgetMixin._model_fallback_provider_for_call


class _RetryProvider:
    def __init__(self, error=None):
        self.attempts = []
        self.error = error

    async def text_chat_stream(self, *, request_max_retries=None, **kwargs):
        self.attempts.append(request_max_retries)
        if self.error is not None:
            raise self.error
        yield _final("stream result")


class BackgroundLlmRetriesTests(unittest.IsolatedAsyncioTestCase):
    def test_attempt_drafts_preserve_hidden_cards_and_allow_inheritance(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable")
        script = (Path(__file__).resolve().parents[1] / "pages/companion-panel/js/panels/provider-tree.js").read_text(encoding="utf-8")
        check = """
const assert = require('node:assert/strict');
const api = window.PrivateCompanionProviderTree;
const context = {state: {overview: {settings: {
  model_request_max_attempts_overrides: {DAILY_PLAN_PROVIDER_ID: 2, CREATIVE_PROVIDER_ID: 1},
  background_llm_request_max_attempts: 3,
}}, providerRequestAttemptsDraft: {DAILY_PLAN_PROVIDER_ID: ''}}, document: {querySelectorAll: () => [], querySelector: () => null}};
assert.deepEqual(api.currentProviderRequestAttemptsValues(context), {CREATIVE_PROVIDER_ID: 1});
assert.equal(api.currentLlmStreamingValue(context).maxAttempts, 3);
context.state.backgroundLlmAttemptsDraft = '0';
assert.equal(api.currentLlmStreamingValue(context).maxAttempts, 0);
context.document.querySelectorAll = () => [{dataset: {providerRequestAttempts: 'CREATIVE_PROVIDER_ID'}, value: ''}];
assert.deepEqual(api.currentProviderRequestAttemptsValues(context), {});
"""
        result = subprocess.run([node], input="global.window = {};\n" + script + check, capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(0, result.returncode, result.stderr)

    async def test_global_and_plugin_attempts_match_both_transports(self):
        for streaming in (False, True):
            for override, expected in ((0, 2), (1, 1)):
                with self.subTest(streaming=streaming, override=override):
                    provider = _RetryProvider()
                    context = _LlmCallContext(provider)
                    context.get_config = lambda: {"provider_settings": {"request_max_retries": 2}}
                    owner = _LlmCallHarness(context, streaming=streaming)
                    owner.background_llm_request_max_attempts = override
                    await owner._llm_call("plan", task="daily_plan", max_tokens=1200)
                    actual = provider.attempts if streaming else [context.last_kwargs["request_max_retries"]]
                    self.assertEqual([expected], actual)
                    self.assertEqual(expected, owner.usage[0]["request_policy"]["request_max_attempts"])

    async def test_card_override_and_quick_mode_use_the_selected_card(self):
        owner = _LlmCallHarness(_LlmCallContext(), streaming=False)
        owner.background_llm_request_max_attempts = 3
        owner.model_request_max_attempts_overrides = {"DAILY_PLAN_PROVIDER_ID": 1, "COMPLEX_REASONING_PROVIDER_ID": 2}
        self.assertEqual(1, owner._background_llm_request_policy(task="daily_plan")["request_max_attempts"])
        owner.provider_config_mode = "quick"
        self.assertEqual(2, owner._background_llm_request_policy(task="daily_plan")["request_max_attempts"])
        await owner._llm_call("unknown task", task="custom", timeout_key="DAILY_PLAN_PROVIDER_ID")
        self.assertEqual(2, owner.context.last_kwargs["request_max_retries"])

    async def test_native_tools_inherit_global_attempts(self):
        context = _LlmCallContext()
        context.get_config = lambda: {"provider_settings": {"request_max_retries": 2}}
        owner = _LlmCallHarness(context, streaming=False)
        await owner._llm_tool_call("plan", tools=None, task="daily_plan")
        self.assertEqual(2, context.last_kwargs["request_max_retries"])

    async def test_legacy_provider_does_not_receive_sdk_unknown_parameter(self):
        class Legacy:
            async def text_chat(self, **kwargs):
                pass

            async def text_chat_stream(self, **kwargs):
                self.kwargs = kwargs
                yield _final("legacy")

        for streaming in (False, True):
            provider = Legacy()
            context = _LlmCallContext(provider)
            context.get_provider_by_id = lambda _id: provider
            owner = _LlmCallHarness(context, streaming=streaming)
            self.assertIsNotNone(await owner._llm_call("plan", max_tokens=1200))
            kwargs = provider.kwargs if streaming else context.last_kwargs
            self.assertNotIn("request_max_retries", kwargs)
            self.assertFalse(owner.usage[0]["request_policy"]["request_retry_supported"])

    async def test_timeout_defers_same_request_without_replaying_or_fallback(self):
        from httpx import ReadTimeout

        for streaming in (False, True):
            provider = _RetryProvider(ReadTimeout("read timed out"))
            context = _LlmCallContext(provider)
            if not streaming:
                async def fail(**kwargs):
                    context.llm_generate_calls += 1
                    raise ReadTimeout("read timed out")
                context.llm_generate = fail
            owner = _LlmCallHarness(context, streaming=streaming)
            owner.model_fallback_overrides = {"DAILY_PLAN_PROVIDER_ID": "backup"}
            self.assertIsNone(await owner._llm_call("plan", task="daily_plan", max_tokens=1200))
            retry_after = owner.usage[0]["request_policy"]["retry_after"]
            self.assertGreater(retry_after, time.time())
            self.assertIsNone(await owner._llm_call("plan", task="daily_plan", max_tokens=1200))
            self.assertEqual(1, len(owner.usage))
            self.assertEqual(0 if streaming else 1, context.llm_generate_calls)
            self.assertEqual(1 if streaming else 0, len(provider.attempts))
            with patch("astrbot_plugin_private_companion.token_budget.time.time", return_value=retry_after + 1):
                await owner._llm_call("plan", task="daily_plan", max_tokens=1200)
            self.assertEqual(2, len(owner.usage))

    async def test_unconfigured_timeout_reports_original_error(self):
        owner = _LlmCallHarness(_LlmCallContext(_RetryProvider(asyncio.TimeoutError())), streaming=True)
        await owner._llm_call("plan", task="daily_plan", max_tokens=1200)
        self.assertIn("调用超时", owner.usage[0]["error"])
        self.assertNotIn("NoneType", owner.usage[0]["error"])

    def test_invalid_overrides_inherit_without_creating_zero_attempt_requests(self):
        owner = _LlmCallHarness(_LlmCallContext(), streaming=False)
        owner.context.get_config = lambda: {"provider_settings": {"request_max_retries": "2"}}
        for value in (None, False, -1, "bad", 1.5, float("inf"), [], {}):
            owner.background_llm_request_max_attempts = value
            self.assertEqual(2, owner._background_llm_request_policy()["request_max_attempts"])
        raw = {"DAILY_PLAN_PROVIDER_ID": "1", "LLM_PROVIDER_ID": -1, "unknown": 2}
        self.assertEqual({"DAILY_PLAN_PROVIDER_ID": 1}, owner._normalize_model_request_max_attempts_overrides(json.dumps(raw)))

    def test_usage_does_not_infer_actual_attempt_count(self):
        owner = _LlmCallHarness(_LlmCallContext(), streaming=False)
        owner.data = {}
        TokenBudgetMixin._record_llm_usage(owner, provider_id="primary", task="daily_plan", prompt="plan", completion="ok", elapsed_ms=10, success=True, request_policy={"request_max_attempts": 2, "request_retry_source": "astrbot_global", "request_retry_supported": True})
        recent = owner.data["token_usage"]["recent"][0]
        self.assertIsNone(recent["provider_attempts"])
        self.assertEqual(2, recent["request_max_attempts"])

    def test_settings_api_normalizes_and_applies_attempts(self):
        from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi

        owner = _LlmCallHarness(_LlmCallContext(), streaming=False)
        api = object.__new__(PrivateCompanionPageApi)
        api.plugin = owner
        api._schema_key_index_cache = None
        values = {"background_llm_request_max_attempts": "1", "model_request_max_attempts_overrides": {"DAILY_PLAN_PROVIDER_ID": 2}}
        for key, value in values.items():
            self.assertIn(key, api._allowed_setting_keys())
            normalized = api._normalize_setting_value(key, value)
            api._apply_config_value(key, normalized)
        self.assertEqual(1, owner.background_llm_request_max_attempts)
        self.assertEqual({"DAILY_PLAN_PROVIDER_ID": 2}, owner.model_request_max_attempts_overrides)
