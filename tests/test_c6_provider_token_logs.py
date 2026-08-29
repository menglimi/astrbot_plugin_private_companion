from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _install_astrbot_stubs() -> None:
    """Install only the AstrBot surface imported by token_budget."""
    astrbot = sys.modules.get("astrbot")
    if not isinstance(astrbot, types.ModuleType):
        astrbot = types.ModuleType("astrbot")
        sys.modules["astrbot"] = astrbot
    api = sys.modules.get("astrbot.api")
    if not isinstance(api, types.ModuleType):
        api = types.ModuleType("astrbot.api")
        sys.modules["astrbot.api"] = api

    logger = getattr(api, "logger", None)
    if logger is None:
        class _Logger:
            def __getattr__(self, _name: str):
                return lambda *args, **kwargs: None

        api.logger = _Logger()
    else:
        for method_name in ("debug", "info", "warning", "error", "exception"):
            if not hasattr(logger, method_name):
                setattr(logger, method_name, lambda *args, **kwargs: None)


def _load_mixin():
    _install_astrbot_stubs()
    package_name = "c6_provider_token_logs_companion"
    package = types.ModuleType(package_name)
    package.__path__ = [str(ROOT)]
    sys.modules[package_name] = package
    module = importlib.import_module(f"{package_name}.token_budget")
    return module.TokenBudgetMixin


TokenBudgetMixin = _load_mixin()


class _MetaProvider:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id

    def meta(self):
        return types.SimpleNamespace(id=self.provider_id)


class _Context:
    def __init__(self, provider=None, responses=None):
        self.provider = provider
        self.responses = list(responses or [])
        self.calls = []

    def get_using_provider(self, *args, **kwargs):
        return self.provider

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _Response:
    def __init__(self, text: str):
        self.completion_text = text


class _Host(TokenBudgetMixin):
    def __init__(self, *, context=None, provider_id=""):
        self.context = context
        self.llm_provider_id = provider_id
        self.data = {}
        self.daily_token_limit = 0
        self.daily_token_soft_limit = 0
        self.enable_daily_token_soft_limit = True
        self.model_fallback_overrides = {}
        self.saved = 0

    def _save_data_sync(self, **_kwargs):
        self.saved += 1


class ProviderResolutionTests(unittest.TestCase):
    def test_explicit_provider_wins_over_context_provider(self):
        host = _Host(context=_Context(_MetaProvider("context-provider")), provider_id="configured-provider")

        self.assertEqual("explicit-provider", host._resolve_chat_provider_id("explicit-provider"))
        self.assertEqual("configured-provider", host._resolve_chat_provider_id())

    def test_context_meta_and_config_are_used_when_no_explicit_provider(self):
        host = _Host(context=_Context(_MetaProvider("meta-provider")))
        self.assertEqual("meta-provider", host._resolve_chat_provider_id())

        class ConfigOnlyProvider:
            provider_config = {"provider_id": "config-provider"}

        config_host = _Host(context=_Context(ConfigOnlyProvider()))
        self.assertEqual("config-provider", config_host._resolve_chat_provider_id())

    def test_missing_provider_registry_returns_empty_string(self):
        host = _Host(context=_Context(None))
        self.assertEqual("", host._resolve_chat_provider_id())


class ProviderFallbackTests(unittest.TestCase):
    def test_failed_primary_provider_uses_fallback_without_leaking_exception(self):
        secret = "PRIMARY_PROVIDER_SECRET_SHOULD_NOT_BE_RETURNED"
        context = _Context(responses=[RuntimeError(secret), _Response("备用 provider 成功")])
        host = _Host(context=context, provider_id="primary-provider")
        host.model_fallback_overrides = {"FAST_RESPONSE_PROVIDER_ID": "fallback-provider"}

        result = asyncio.run(host._llm_call("hello", task="voice", timeout_key="FAST_RESPONSE_PROVIDER_ID"))

        self.assertEqual("备用 provider 成功", result)
        self.assertNotIn(secret, result or "")
        self.assertEqual(["primary-provider", "fallback-provider"], [item["chat_provider_id"] for item in context.calls])
        recent = host.data["token_usage"]["recent"]
        self.assertTrue(any(item["provider"] == "fallback-provider" and item["success"] for item in recent))

    def test_system_prompt_is_included_in_fallback_usage_estimate(self):
        context = _Context(responses=[_Response("ok")])
        host = _Host(context=context, provider_id="primary-provider")

        result = asyncio.run(
            host._llm_call(
                "dynamic user prompt",
                system_prompt="stable system prompt",
                task="detail",
            )
        )

        self.assertEqual("ok", result)
        self.assertEqual("stable system prompt", context.calls[0]["system_prompt"])
        self.assertEqual(
            len("stable system prompt\n\ndynamic user prompt"),
            host.data["token_usage"]["recent"][-1]["prompt_chars"],
        )


class TokenLogBoundaryTests(unittest.TestCase):
    def test_usage_merges_partial_sources_and_provider_aliases(self):
        host = _Host()

        class Response:
            usage = types.SimpleNamespace(prompt_tokens=120)
            usage_metadata = {
                "candidates_token_count": 30,
                "thoughts_token_count": 10,
                "total_token_count": 160,
                "cached_content_token_count": 40,
            }

        usage = host._extract_llm_usage(Response(), "prompt", "completion")
        self.assertEqual(120, usage["prompt_tokens"])
        self.assertEqual(40, usage["completion_tokens"])
        self.assertEqual(10, usage["reasoning_tokens"])
        self.assertEqual(160, usage["total_tokens"])
        self.assertEqual(40, usage["cached_tokens"])
        self.assertFalse(usage["estimated"])

    def test_usage_reads_anthropic_and_cache_aliases(self):
        host = _Host()
        response = types.SimpleNamespace(
            raw_response={
                "usage": {
                    "input_tokens": 80,
                    "output_tokens": 20,
                    "cache_read_input_tokens": 12,
                    "cache_creation_input_tokens": 5,
                }
            }
        )
        usage = host._extract_llm_usage(response, "prompt", "completion")
        self.assertEqual(80, usage["prompt_tokens"])
        self.assertEqual(20, usage["completion_tokens"])
        self.assertEqual(100, usage["total_tokens"])
        self.assertEqual(12, usage["cache_read_tokens"])
        self.assertEqual(5, usage["cache_write_tokens"])

    def test_openai_reasoning_is_not_counted_twice(self):
        host = _Host()
        response = types.SimpleNamespace(
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "completion_tokens_details": {"reasoning_tokens": 20},
            }
        )
        usage = host._extract_llm_usage(response, "prompt", "completion")
        self.assertEqual(100, usage["prompt_tokens"])
        self.assertEqual(50, usage["completion_tokens"])
        self.assertEqual(20, usage["reasoning_tokens"])
        self.assertEqual(150, usage["total_tokens"])

    def test_total_only_usage_does_not_expand_provider_total(self):
        host = _Host()
        response = types.SimpleNamespace(usage={"total_tokens": 3})
        usage = host._extract_llm_usage(response, "a very long prompt body", "")
        self.assertEqual(3, usage["prompt_tokens"])
        self.assertEqual(0, usage["completion_tokens"])
        self.assertEqual(3, usage["total_tokens"])
        self.assertTrue(usage["estimated"])

    def test_usage_metadata_from_model_dump_is_supported(self):
        host = _Host()

        class Response:
            def model_dump(self):
                return {
                    "usage_metadata": {
                        "prompt_token_count": 12,
                        "candidates_token_count": 4,
                        "total_token_count": 16,
                    }
                }

        usage = host._extract_llm_usage(Response(), "prompt", "completion")
        self.assertEqual((12, 4, 16), (
            usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
        ))
        self.assertFalse(usage["estimated"])

    def test_internal_usage_keeps_only_lengths_and_bounded_error(self):
        prompt = "PROMPT_BODY_" + ("p" * 5000)
        completion = "COMPLETION_BODY_" + ("c" * 5000)
        error = "ERROR_BODY_" + ("e" * 1000)
        host = _Host()

        host._record_llm_usage(
            provider_id="provider",
            task="task",
            prompt=prompt,
            completion=completion,
            elapsed_ms=12,
            success=False,
            error=error,
        )

        item = host.data["token_usage"]["recent"][-1]
        self.assertEqual(len(prompt), item["prompt_chars"])
        self.assertEqual(len(completion), item["completion_chars"])
        self.assertLessEqual(len(item["error"]), 160)
        serialized = repr(host.data["token_usage"])
        self.assertNotIn(prompt, serialized)
        self.assertNotIn(completion, serialized)
        self.assertNotIn(error, serialized)

    def test_external_usage_has_bounded_identity_fields_and_no_raw_bodies(self):
        prompt = "EXTERNAL_PROMPT_" + ("p" * 5000)
        completion = "EXTERNAL_COMPLETION_" + ("c" * 5000)
        host = _Host()

        host._record_external_llm_usage(
            provider_id="provider",
            task="external-task",
            prompt=prompt,
            completion=completion,
            elapsed_ms=4,
            success=True,
            session_id="session-" + ("s" * 5000),
            sender_id="sender-" + ("d" * 5000),
            message_type="message-type-" + ("m" * 5000),
        )

        item = host.data["token_usage"]["external"]["recent"][-1]
        self.assertLessEqual(len(item["session"]), 160)
        self.assertLessEqual(len(item["sender"]), 80)
        self.assertLessEqual(len(item["message_type"]), 20)
        self.assertLessEqual(len(item["task"]), 40)
        self.assertEqual(len(prompt), item["prompt_chars"])
        self.assertEqual(len(completion), item["completion_chars"])
        serialized = repr(host.data["token_usage"]["external"])
        self.assertNotIn(prompt, serialized)
        self.assertNotIn(completion, serialized)


if __name__ == "__main__":
    unittest.main()
