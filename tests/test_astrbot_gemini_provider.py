"""AstrBot 的原生 Google GenAI provider 也必须能被 V2 适配器包装（文本与原生视频）。"""

import enum
from http import HTTPStatus
import json
from dataclasses import replace
from types import SimpleNamespace as NS
import unittest
import warnings

import aiohttp
import httpx

try:
    # google-genai 由宿主提供，requirements.txt 不声明它：契约环境里可能没有。
    from astrbot_host.video.gemini_provider import GeminiNativeVideoProvider
    from google.genai import errors as genai_errors
except ModuleNotFoundError:  # 只在没装宿主 SDK 的环境里发生，视频与真实 SDK 异常契约随之跳过。
    GeminiNativeVideoProvider = None
    genai_errors = None
from companion_v2.adapters.astrbot.memory_output import capture, explicit_refusal
from companion_v2.adapters.astrbot.provider import (AstrBotTextProvider, UsageTariff, _gemini_messages,
                                                    provider_fingerprint)
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings
from companion_v2.adapters.astrbot.provider_transport import (extract_error_status_code,
                                                              rejected_before_generation)
from companion_v2.adapters.astrbot.response_diagnostics import native_finish_reason, observe_completion
from companion_v2.execution.models import ProviderBinding, TaskError
from companion_v2.model_gateway.budget import BudgetLedger
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, BudgetPolicy, ModelEnvelope
from companion_v2.model_gateway.service import LocalModelGateway
from tests.test_model_gateway import ModelFixture


class StubGeminiProvider:
    """只实现 AstrBot ProviderGoogleGenAI 对 V2 暴露的那部分表面。"""

    def __init__(self):
        self.provider_config = {"id": "gemini-native", "type": "googlegenai_chat_completion",
                                "model": "gemini-3.8-flash"}
        self.client = object()
        self.calls = []

    def get_model(self):
        return "gemini-3.8-flash"

    async def _query(self, payload, tools, *, request_max_retries=None):
        self.calls.append((payload, tools, request_max_retries))
        return NS(id="gemini-response-1", completion_text="好的", tools_call_name=[],
                  tools_call_ids=[], tools_call_args=[],
                  usage=NS(input_other=10, input_cached=2, output=5))


class StubContext:
    def __init__(self, provider):
        self.provider = provider

    def get_provider_by_id(self, provider_id):
        return self.provider


def make_provider(streaming=False):
    native = StubGeminiProvider()
    provider = AstrBotTextProvider(
        StubContext(native), bridge=NS(),
        binding=ProviderBinding("model.text", "gemini-native", "4.29.1", "generation-1"),
        provider_id="gemini-native", version="4.29.1", provider_type=(StubGeminiProvider,),
        function_tool_type=object, tool_set_type=object, tariff=UsageTariff(3, 1, 9),
        maximum_output_tokens=1024, streaming=streaming)
    return provider, native


class UnrelatedProvider:
    """占位类：模拟未登记进 native_types 的宿主 provider 类型。"""


class StubAnthropicProvider:
    """未登记进 native_types 的 provider：只靠公共表面被接受。"""

    def __init__(self):
        self.provider_config = {"id": "anthropic-native", "type": "anthropic_chat_completion",
                                "model": "claude-x"}
        self.client = object()
        self.calls = []

    def get_model(self):
        return "claude-x"

    async def _query(self, payload, tools, *, request_max_retries=None):
        self.calls.append((payload, tools, request_max_retries))
        return NS(id="anthropic-response-1", completion_text="好的", tools_call_name=[],
                  tools_call_ids=[], tools_call_args=[],
                  usage=NS(input_other=3, input_cached=0, output=2))


class GeminiTextProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_gemini_provider_uses_its_own_transport(self):
        provider, _native = make_provider(streaming=True)
        self.assertEqual(provider.kind, "gemini")
        # 原生 Gemini 的流式协议与 V2 的流式校验器不兼容：构造时已降级。
        self.assertFalse(provider.streaming)
        local = provider.local_provider()
        self.assertEqual(local.transport, "astrbot_gemini")
        self.assertEqual(local.model, "gemini-3.8-flash")

    async def test_unregistered_native_provider_is_wrapped_by_surface(self):
        native = StubAnthropicProvider()
        provider = AstrBotTextProvider(
            StubContext(native), bridge=NS(),
            binding=ProviderBinding("model.text", "anthropic-native", "4.29.1", "generation-1"),
            provider_id="anthropic-native", version="4.29.1", provider_type=(UnrelatedProvider,),
            function_tool_type=object, tool_set_type=object, tariff=UsageTariff(3, 1, 9),
            maximum_output_tokens=1024)
        self.assertFalse(provider.verified_type)
        self.assertEqual(provider.kind, "native")
        self.assertEqual(provider.local_provider().transport, "astrbot_native")
        await provider.probe()
        payload, _tools, retries = native.calls[0]
        self.assertEqual(retries, 1)
        self.assertEqual(payload["max_tokens"], 256)

    async def test_probe_sends_the_gemini_payload(self):
        provider, native = make_provider()
        await provider.probe()
        payload, tools, retries = native.calls[0]
        self.assertIsNone(tools)
        self.assertEqual(retries, 1)
        self.assertEqual(payload["model"], "gemini-3.8-flash")
        self.assertEqual(payload["max_tokens"], 256)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Reply OK."}])

    async def test_token_usage_is_rated_like_openai_usage(self):
        provider, _native = make_provider()
        amount = provider._usage(NS(usage=NS(input_other=10, input_cached=2, output=5)))
        self.assertEqual(amount.input_tokens, 12)
        self.assertEqual(amount.cached_input_tokens, 2)
        self.assertEqual(amount.output_tokens, 5)
        self.assertEqual(amount.tokens, 17)

    async def test_tool_results_gain_the_function_name_for_gemini(self):
        messages = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "call-1", "type": "function",
                             "function": {"name": "weather.lookup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "{\"ok\": true}"},
        ]
        enriched = _gemini_messages(messages)
        self.assertEqual(enriched[1]["name"], "weather.lookup")
        self.assertNotIn("name", messages[1])

    async def test_voice_material_reaches_gemini_as_a_data_url_part(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "听一下这段"},
            {"type": "input_audio", "input_audio": {"data": "QUJD", "format": "wav"}},
        ]}]
        converted = _gemini_messages(messages)
        self.assertEqual(converted[0]["content"][0], {"type": "text", "text": "听一下这段"})
        self.assertEqual(converted[0]["content"][1],
                         {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64,QUJD"}})
        # 宿主拿到的是副本：原始 wire 载荷不能被就地改写。
        self.assertEqual(messages[0]["content"][1]["type"], "input_audio")

    async def test_text_only_messages_are_returned_unchanged(self):
        messages = [{"role": "user", "content": "在吗"}]
        self.assertIs(_gemini_messages(messages), messages)


class FinishReason(enum.Enum):
    """google.genai.types.FinishReason 的形状：枚举成员，str() 带类名前缀。"""

    STOP = "STOP"
    MAX_TOKENS = "MAX_TOKENS"
    SAFETY = "SAFETY"


def native_completion(reason, text="好的"):
    """AstrBot 原生 Gemini 调用返回的 LLMResponse：归一化正文 + 原始 SDK 响应。"""

    return NS(id="gemini-response-1", completion_text=text, tools_call_name=[], tools_call_ids=[],
              tools_call_args=[], raw_completion=NS(candidates=[NS(finish_reason=reason)]))


class NativeFinishReasonTests(unittest.TestCase):
    """原生 provider 的结束原因只能从 SDK 响应里读，不能退回成「正常说完」。"""

    def test_candidate_reason_is_reported_in_the_gateway_classification(self):
        for reason, expected in ((FinishReason.STOP, "stop"), (FinishReason.MAX_TOKENS, "length"),
                                 (FinishReason.SAFETY, "content_filter")):
            with self.subTest(reason=reason):
                self.assertEqual(observe_completion(native_completion(reason)).finish_reason, expected)

    def test_truncated_native_reply_is_captured_as_length_instead_of_stop(self):
        self.assertEqual(capture(native_completion(FinishReason.MAX_TOKENS)).finish_reason, "length")
        self.assertEqual(capture(native_completion(FinishReason.SAFETY)).finish_reason, "refusal")
        self.assertTrue(explicit_refusal(native_completion(FinishReason.SAFETY, "")))
        self.assertFalse(explicit_refusal(native_completion(FinishReason.MAX_TOKENS, "半句")))
        self.assertEqual(capture(native_completion(FinishReason.STOP)).finish_reason, "stop")

    def test_plain_string_reason_from_another_sdk_build_is_accepted(self):
        self.assertEqual(native_finish_reason(native_completion("MAX_TOKENS")), "length")
        self.assertEqual(native_finish_reason(native_completion("FinishReason.MAX_TOKENS")), "length")

    def test_completion_without_a_candidate_keeps_the_previous_classification(self):
        bare = NS(completion_text="好的")
        self.assertIsNone(native_finish_reason(bare))
        self.assertEqual(observe_completion(bare).finish_reason, "missing")
        self.assertEqual(capture(bare).finish_reason, "stop")

    def test_unmapped_reason_is_unknown_rather_than_guessed(self):
        self.assertEqual(observe_completion(native_completion("MALFORMED_FUNCTION_CALL")).finish_reason, "unknown")
        self.assertEqual(capture(native_completion("MALFORMED_FUNCTION_CALL")).finish_reason, "invalid")

    def test_openai_choices_still_decide_when_the_completion_has_them(self):
        completion = NS(choices=[NS(finish_reason="length", message=NS(content="半句"))],
                        raw_completion=NS(candidates=[NS(finish_reason=FinishReason.STOP)]))
        self.assertEqual(observe_completion(completion).finish_reason, "length")
        self.assertEqual(capture(completion).finish_reason, "length")


class _Bridge:
    async def verify_role(self, *_args, **_kwargs):
        return True


class CeilingGeminiProvider(StubGeminiProvider):
    """A native Gemini provider whose body stopped at ``max_tokens``."""

    def __init__(self, text, *, finish="MAX_TOKENS"):
        super().__init__()
        self.body = text
        self.finish = finish

    async def _query(self, payload, tools, *, request_max_retries=None):
        self.calls.append((payload, tools, request_max_retries))
        return NS(id="gemini-response-1", completion_text=self.body, tools_call_name=[],
                  tools_call_ids=[], tools_call_args=[],
                  usage=NS(input_other=10, input_cached=2, output=5),
                  raw_completion=NS(candidates=[NS(finish_reason=NS(name=self.finish))]))


def ceiling_provider(text, *, finish="MAX_TOKENS"):
    native = CeilingGeminiProvider(text, finish=finish)
    provider = AstrBotTextProvider(
        StubContext(native), bridge=_Bridge(),
        binding=ProviderBinding("model.text", "gemini-native", "4.29.1", "generation-1"),
        provider_id="gemini-native", version="4.29.1", provider_type=(CeilingGeminiProvider,),
        function_tool_type=object, tool_set_type=object, tariff=UsageTariff(3, 1, 9),
        maximum_output_tokens=1024)
    return provider, native


class GeminiCeilingTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """The normalized Gemini response carries its ceiling; the capture must read it."""

    def test_a_cut_off_gemini_body_is_not_reported_as_a_complete_one(self):
        cut = NS(completion_text='{"items":[{"title":"练琴"', tools_call_args=[],
                 raw_completion=NS(candidates=[NS(finish_reason=NS(name="MAX_TOKENS"))]))
        whole = NS(completion_text='{"items":[]}', tools_call_args=[],
                   raw_completion=NS(candidates=[NS(finish_reason=NS(name="STOP"))]))

        self.assertEqual(capture(cut).finish_reason, "length")
        self.assertEqual(capture(cut).text, '{"items":[{"title":"练琴"')
        self.assertEqual(capture(whole).finish_reason, "stop")

    async def test_a_background_body_cut_by_the_ceiling_reaches_its_owner(self):
        provider, _native = ceiling_provider('{"items":[{"title":"练琴"')
        request = replace(self.request(), output_format="calendar_planning", response_format="json_object")
        envelope = ModelEnvelope(request, "task prompt", 256, "configuration", before_dispatch=lambda: None)

        response = await provider.generate(envelope)

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.captured_output.text, '{"items":[{"title":"练琴"')
        self.assertEqual(response.captured_output.finish_reason, "length")
        self.assertEqual(response.diagnostics.parse_state, "captured")

    async def test_a_reply_cut_by_the_ceiling_is_never_captured(self):
        provider, _native = ceiling_provider('{"text":"半句')
        envelope = ModelEnvelope(self.request(), "task prompt", 256, "configuration",
                                 before_dispatch=lambda: None)

        response = await provider.generate(envelope)

        self.assertIsNone(response.captured_output)
        self.assertEqual(response.diagnostics.parse_state, "contract_rejected")


class StubVideoProvider:
    """只实现原生视频适配器会用到的那部分 Google GenAI Provider 表面。"""

    def __init__(self, *, model="gemini-3.8-flash", config=None, reply=None):
        self.provider_config = {"id": "gemini-native", "type": "googlegenai_chat_completion",
                                "model": model, "api_key": ["secret-value"]}
        if config:
            self.provider_config.update(config)
        self.default_params = {"temperature": 1.0}
        self.reply = reply or json.dumps({"text": "在的。", "claims": [{
            "id": "c1", "kind": "subjective", "statement": "在的。", "evidence": None}]},
            ensure_ascii=False)
        self.calls = []
        self.client = NS(models=NS(generate_content=self.generate_content))

    def meta(self):
        return NS(id="gemini-native")

    def get_model(self):
        return self.provider_config["model"]

    async def _prepare_query_config(self, payloads, tools, choice, instruction, modalities, temperature):
        self.calls.append(("config", payloads))
        return {}

    async def generate_content(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return NS(candidates=[NS(finish_reason=NS(name="STOP"),
                                 content=NS(parts=[NS(text=self.reply, thought=False)]))])


class VideoCapabilityLostProvider:
    """同一 ID 下换成了另一种 Provider：原生视频能力已经不存在。"""

    provider_config = {"id": "gemini-native", "type": "openai_chat_completion",
                       "model": "gemini-3.8-flash"}
    default_params = {}

    def meta(self):
        return NS(id="gemini-native")

    def get_model(self):
        return self.provider_config["model"]


def clone_video_provider_class(name):
    """模拟 AstrBot 重新导入适配器模块后得到的同名新类。"""
    body = {key: value for key, value in StubVideoProvider.__dict__.items()
            if key not in ("__dict__", "__weakref__", "__module__", "__qualname__", "__doc__")}
    return type(name, (), body)


def make_video_adapter(providers):
    """``providers`` 由测试改写，用来模拟宿主刷新 Provider 注册表。"""
    native = StubVideoProvider()
    providers["gemini-native"] = native
    adapter = GeminiNativeVideoProvider(
        NS(get_provider_by_id=lambda provider_id: providers.get(provider_id)), _Bridge(),
        NS(video_provider_type=StubVideoProvider, version="4.29.1"), native,
        settings=TextProviderSettings("gemini-native", UsageTariff(1, 1, 1),
                                      maximum_output_tokens=128, maximum_input_estimate=131072))
    return adapter, native


@unittest.skipUnless(GeminiNativeVideoProvider is not None, "google-genai 由宿主提供，未安装时跳过视频适配器契约")
class NativeVideoProviderIdentityTests(unittest.IsolatedAsyncioTestCase):
    """宿主刷新注册表后的原生视频适配器：等价重建续接，真实变化拒绝。"""

    def setUp(self):
        self.providers = {}
        self.adapter, self.native = make_video_adapter(self.providers)

    async def test_a_stable_provider_is_never_rebound(self):
        binding = self.adapter.binding
        await self.adapter.probe()
        self.assertIs(self.adapter.native, self.native)
        self.assertIs(self.adapter.binding, binding)
        self.assertEqual(self.adapter.binding.generation, self.adapter.fingerprint)
        self.assertTrue(self.native.calls)

    async def test_a_rebuilt_instance_is_rebound_and_the_call_continues(self):
        replacement = StubVideoProvider()
        # 宿主重新合并配置时可能补写 modalities 这类易变派生字段：指纹保持稳定，实例仍可续接。
        replacement.provider_config["modalities"] = []
        self.providers["gemini-native"] = replacement
        generation = self.adapter.fingerprint

        await self.adapter.probe()

        self.assertIs(self.adapter.native, replacement)
        self.assertEqual(self.adapter.fingerprint, generation)
        self.assertEqual(self.adapter.fingerprint, provider_fingerprint(replacement))
        self.assertEqual(self.adapter.binding.generation, self.adapter.fingerprint)
        self.assertTrue(replacement.calls)
        self.assertEqual(self.native.calls, [])

    async def test_in_place_config_merge_refreshes_the_fingerprint(self):
        self.native.provider_config["modalities"] = ["text", "image", "video"]

        await self.adapter.probe()

        self.assertIs(self.adapter.native, self.native)
        self.assertEqual(self.adapter.fingerprint, provider_fingerprint(self.native))
        self.assertEqual(self.adapter.binding.generation, self.adapter.fingerprint)

    async def test_a_reimported_host_class_is_the_same_provider(self):
        replacement = clone_video_provider_class("StubVideoProvider")()
        self.providers["gemini-native"] = replacement

        await self.adapter.probe()

        self.assertIs(self.adapter.native, replacement)
        self.assertEqual(self.adapter.binding.generation, self.adapter.fingerprint)

    async def test_a_missing_provider_is_still_rejected(self):
        self.providers["gemini-native"] = None

        with self.assertRaises(TaskError) as raised:
            self.adapter._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("unavailable", str(raised.exception))

    async def test_a_changed_provider_type_is_still_rejected(self):
        self.providers["gemini-native"] = VideoCapabilityLostProvider()

        with self.assertRaises(TaskError) as raised:
            self.adapter._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("type changed", str(raised.exception))

    async def test_a_changed_model_is_still_rejected(self):
        self.providers["gemini-native"] = StubVideoProvider(model="gemini-4.0-pro")

        with self.assertRaises(TaskError) as raised:
            self.adapter._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("model_changed", str(raised.exception))

    async def test_a_changed_captured_field_is_rejected_without_its_value(self):
        self.providers["gemini-native"] = StubVideoProvider(config={"api_key": ["another-secret"]})

        with self.assertRaises(TaskError) as raised:
            self.adapter._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("api_key", str(raised.exception))
        self.assertNotIn("another-secret", str(raised.exception))

    async def test_changed_default_parameters_are_still_rejected(self):
        replacement = StubVideoProvider()
        replacement.default_params = {"top_p": 0.2}
        self.providers["gemini-native"] = replacement

        with self.assertRaises(TaskError) as raised:
            self.adapter._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("parameters_changed", str(raised.exception))


@unittest.skipUnless(GeminiNativeVideoProvider is not None, "google-genai 由宿主提供，未安装时跳过视频适配器契约")
class NativeVideoTextTaskTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """刷新后的文本任务（如主动联系）也必须继续走通原生视频适配器。"""

    async def test_a_text_task_survives_a_registry_refresh(self):
        providers = {}
        adapter, native = make_video_adapter(providers)
        replacement = StubVideoProvider()
        replacement.provider_config["modalities"] = ["text", "video"]
        providers["gemini-native"] = replacement
        request = replace(self.request(), role_instruction="角色",
                          role_guard_instruction="AstrBot 人格原文")
        envelope = ModelEnvelope(request, "task prompt", 128, "configuration",
                                 before_dispatch=lambda: None)

        response = await adapter.generate(envelope)

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.draft.text, "在的。")
        self.assertIs(adapter.native, replacement)
        self.assertEqual(native.calls, [])
        self.assertTrue(any(kind == "generate" for kind, _payload in replacement.calls))
        self.assertEqual(adapter.binding.generation, adapter.fingerprint)


class SimulatedAPIError(Exception):
    """模拟 google.genai.errors.APIError 的属性形状：HTTP 状态在 .code，没有 .status_code。"""

    def __init__(self, code, message="private provider diagnostic", *, status="INVALID_ARGUMENT", response=None):
        super().__init__(f"{code} {status}. {message}")
        self.code = code
        self.status = status
        self.response = response


class SimulatedClientError(SimulatedAPIError):
    pass


class SimulatedServerError(SimulatedAPIError):
    pass


class FailingGeminiProvider(StubGeminiProvider):
    def __init__(self, error_factory, *, provider_id="gemini-native"):
        super().__init__()
        self.provider_config["id"] = provider_id
        self.error_factory = error_factory

    async def _query(self, payload, tools, *, request_max_retries=None):
        self.calls.append((payload, tools, request_max_retries))
        raise self.error_factory()


class ErrorStatusExtractionTests(unittest.TestCase):
    """extract_error_status_code 与 rejected_before_generation 的统一 HTTP 状态码边界。"""

    def test_extracts_from_status_code_code_http_status_response_and_cause(self):
        self.assertEqual(extract_error_status_code(NS(status_code=400)), 400)
        self.assertEqual(extract_error_status_code(SimulatedClientError(403, status="PERMISSION_DENIED")), 403)
        self.assertEqual(extract_error_status_code(NS(status_code=None, code=401)), 401)
        self.assertEqual(extract_error_status_code(NS(http_status=422)), 422)
        self.assertEqual(extract_error_status_code(NS(response=NS(status_code=404))), 404)
        self.assertEqual(extract_error_status_code(NS(response=NS(status=503))), 503)
        extracted = extract_error_status_code(NS(status_code=HTTPStatus.BAD_REQUEST))
        self.assertEqual(extracted, 400)
        self.assertIs(type(extracted), int)

        wrapped = RuntimeError("outer failure")
        wrapped.__cause__ = SimulatedClientError(403)
        self.assertEqual(extract_error_status_code(wrapped), 403)
        self.assertTrue(rejected_before_generation(wrapped))

        cyclic = RuntimeError("cycle")
        cyclic.__cause__ = cyclic
        self.assertIsNone(extract_error_status_code(cyclic))
        self.assertFalse(rejected_before_generation(cyclic))

    def test_rejects_booleans_strings_none_and_out_of_range_codes(self):
        for bad in (
            None,
            TaskError("revoked", "owner cancelled"),
            Exception("plain"),
            NS(code=True),
            NS(code=False),
            NS(code=None),
            NS(code=0),
            NS(code=99),
            NS(code=600),
            NS(code=1002),
            NS(code="400"),
            NS(code=400.0),
            NS(status_code=True, code=False, http_status=999, response=NS(status_code=True, status=99)),
        ):
            with self.subTest(bad=bad):
                self.assertIsNone(extract_error_status_code(bad))
                self.assertFalse(rejected_before_generation(bad))

    def test_handles_httpx_and_aiohttp_errors_without_deprecation_warnings(self):
        request = httpx.Request("POST", "https://example.invalid")
        httpx_error = httpx.HTTPStatusError("bad request", request=request, response=httpx.Response(422, request=request))
        self.assertEqual(extract_error_status_code(httpx_error), 422)
        self.assertTrue(rejected_before_generation(httpx_error))

        aiohttp_error = aiohttp.ClientResponseError(NS(real_url="https://example.invalid"), (), status=401, message="unauthorized")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            self.assertEqual(extract_error_status_code(aiohttp_error), 401)
            self.assertTrue(rejected_before_generation(aiohttp_error))
        self.assertEqual([w for w in caught if issubclass(w.category, DeprecationWarning)], [])

    def test_preserves_rejection_vs_503_and_429_classification_contract(self):
        for code in (400, 401, 403, 404, 405, 415, 422):
            with self.subTest(rejection_code=code):
                self.assertTrue(rejected_before_generation(SimulatedClientError(code)))
        for code in (429, 500, 502, 503, 504):
            with self.subTest(ambiguous_or_server_code=code):
                self.assertFalse(rejected_before_generation(SimulatedServerError(code)))


class GeminiErrorStatusTests(ModelFixture, unittest.IsolatedAsyncioTestCase):
    """Google GenAI APIError(.code) 必须被准确提取、记入诊断日志并在 4xx 时触发 not_started 回退。"""

    def _failing_provider(self, error_factory, *, provider_id="gemini-native"):
        native = FailingGeminiProvider(error_factory, provider_id=provider_id)
        provider = AstrBotTextProvider(
            StubContext(native), bridge=_Bridge(),
            binding=ProviderBinding("model.text", provider_id, "4.29.1", "generation-1"),
            provider_id=provider_id, version="4.29.1", provider_type=(FailingGeminiProvider,),
            function_tool_type=object, tool_set_type=object, tariff=UsageTariff(3, 1, 9),
            maximum_output_tokens=1024)
        return provider, native

    def _enable_large_budget(self):
        ceiling = Amount(100_000, 1_000_000)
        self.ledger = BudgetLedger(self.runtime, BudgetPolicy(ceiling, ceiling, ceiling, ceiling, ceiling))
        self.gateway = LocalModelGateway(self.router, self.ledger, self.tasks)

    async def test_simulated_4xx_code_marks_not_started_and_logs_status_code(self):
        secret = "private gemini credential secret"
        for code in (400, 401, 403, 422):
            with self.subTest(code=code):
                provider, _native = self._failing_provider(lambda c=code: SimulatedClientError(c, secret))
                envelope = ModelEnvelope(self.request(), "task prompt", 256, "configuration",
                                         before_dispatch=lambda: None)
                with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
                    response = await provider.generate(envelope)
                self.assertEqual(response.status, "not_started")
                self.assertEqual(response.usage, Amount(0, 0))
                self.assertEqual(response.failure, ModelFailure.TRANSPORT)
                self.assertEqual(response.diagnostics.status_code, code)
                self.assertEqual(response.diagnostics.error_type, "SimulatedClientError")
                logs = "\n".join(captured.output)
                self.assertIn(f"status_code={code}", logs)
                self.assertIn("标记为未开始", logs)
                self.assertNotIn(secret, logs)

    async def test_simulated_503_and_429_stay_unknown_and_log_status_code(self):
        secret = "private upstream payload"
        for code, factory in (
            (503, lambda: SimulatedServerError(503, secret, status="UNAVAILABLE")),
            (429, lambda: SimulatedClientError(429, secret, status="RESOURCE_EXHAUSTED")),
        ):
            with self.subTest(code=code):
                provider, _native = self._failing_provider(factory)
                envelope = ModelEnvelope(self.request(), "task prompt", 256, "configuration",
                                         before_dispatch=lambda: None)
                with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
                    response = await provider.generate(envelope)
                self.assertEqual(response.status, "unknown")
                self.assertEqual(response.failure, ModelFailure.TRANSPORT)
                self.assertEqual(response.diagnostics.status_code, code)
                logs = "\n".join(captured.output)
                self.assertIn(f"status_code={code}", logs)
                self.assertNotIn(secret, logs)

    async def test_preparation_task_error_does_not_confuse_string_code_with_http_status(self):
        provider, _native = self._failing_provider(lambda: AssertionError("should not dispatch"))

        def revoked():
            raise TaskError("revoked", "private cancellation reason")

        envelope = ModelEnvelope(self.request(), "task prompt", 256, "configuration",
                                 before_dispatch=revoked)
        with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
            response = await provider.generate(envelope)
        self.assertEqual(response.status, "not_started")
        self.assertEqual(response.failure, ModelFailure.PREPARATION)
        self.assertEqual(response.diagnostics.error_code, "revoked")
        self.assertIsNone(response.diagnostics.status_code)
        logs = "\n".join(captured.output)
        self.assertIn("task_error_code=revoked", logs)
        self.assertIn("status_code=unavailable", logs)
        self.assertNotIn("private cancellation reason", logs)

    def test_response_format_unsupported_also_reads_code_attribute(self):
        self.assertTrue(AstrBotTextProvider._response_format_unsupported(
            SimulatedClientError(400, "response_format is not supported")))
        self.assertTrue(AstrBotTextProvider._response_format_unsupported(
            SimulatedClientError(422, "invalid field response_format")))
        self.assertFalse(AstrBotTextProvider._response_format_unsupported(
            SimulatedServerError(503, "response_format temporary error")))

    async def test_gateway_fallback_triggers_on_simulated_4xx_code(self):
        provider_4xx, _ = self._failing_provider(
            lambda: SimulatedClientError(403, "forbidden"), provider_id="primary")
        self.gateway_with(provider_4xx.local_provider(), self.provider("fallback"))
        self._enable_large_budget()
        completion = await self.gateway.generate(self.model_run("task-4xx"), self.request())
        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual([name for name, _ in self.called], ["fallback"])
        calls = self.gateway.observe(self.context, "task-4xx").unwrap()["calls"]
        self.assertEqual([(c["state"], c["actual_tokens"]) for c in calls], [("not_started", 0), ("generated", 15)])
        self.assertEqual(calls[0]["response_diagnostics"]["status_code"], 403)
        self.assertTrue(all(a["spent_tokens"] == 15 and a["held_tokens"] == 0 for a in self.accounts()))

    async def test_gateway_does_not_fallback_on_simulated_503_code(self):
        provider_503, _ = self._failing_provider(
            lambda: SimulatedServerError(503, "unavailable", status="UNAVAILABLE"), provider_id="primary")
        self.gateway_with(provider_503.local_provider(), self.provider("fallback"))
        self._enable_large_budget()
        with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
            with self.assertRaises(TaskError) as raised:
                await self.gateway.generate(self.model_run("task-503"), self.request())
        self.assertEqual(raised.exception.code, "model_usage_unknown")
        self.assertEqual(self.called, [])
        self.assertIn("status_code=503", "\n".join(captured.output))
        calls_503 = self.gateway.observe(self.context, "task-503").unwrap()["calls"]
        self.assertEqual(calls_503[0]["state"], "unknown")
        self.assertEqual(calls_503[0]["response_diagnostics"]["status_code"], 503)

    @unittest.skipUnless(genai_errors is not None, "google-genai 由宿主提供，未安装时跳过真实 SDK 异常契约")
    async def test_real_google_genai_client_error_4xx_logging_and_gateway_fallback(self):
        secret = "private gemini api error payload"
        for code in (400, 401, 403, 422):
            with self.subTest(real_client_error=code):
                err = genai_errors.ClientError(code, {"error": {"code": code, "message": secret, "status": "INVALID_ARGUMENT"}})
                self.assertFalse(hasattr(err, "status_code"))
                self.assertEqual(extract_error_status_code(err), code)
                self.assertTrue(rejected_before_generation(err))

                provider, _ = self._failing_provider(lambda e=err: e, provider_id="primary")
                envelope = ModelEnvelope(self.request(), "task prompt", 256, "configuration",
                                         before_dispatch=lambda: None)
                with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
                    response = await provider.generate(envelope)
                self.assertEqual(response.status, "not_started")
                self.assertEqual(response.usage, Amount(0, 0))
                self.assertEqual(response.diagnostics.status_code, code)
                self.assertEqual(response.diagnostics.error_type, "ClientError")
                logs = "\n".join(captured.output)
                self.assertIn(f"status_code={code}", logs)
                self.assertIn("标记为未开始", logs)
                self.assertNotIn(secret, logs)

        # 真实 ClientError(400) 通过 LocalModelGateway 触发备用模型回退且零扣费
        self.called.clear()
        err_400 = genai_errors.ClientError(400, {"error": {"code": 400, "message": secret, "status": "INVALID_ARGUMENT"}})
        primary_400, _ = self._failing_provider(lambda: err_400, provider_id="primary")
        self.gateway_with(primary_400.local_provider(), self.provider("fallback"))
        self._enable_large_budget()
        completion = await self.gateway.generate(self.model_run("real-genai-400"), self.request())
        self.assertEqual(completion.draft.text, "private draft")
        self.assertEqual([name for name, _ in self.called], ["fallback"])
        calls = self.gateway.observe(self.context, "real-genai-400").unwrap()["calls"]
        self.assertEqual([(c["state"], c["actual_tokens"]) for c in calls], [("not_started", 0), ("generated", 15)])
        self.assertEqual(calls[0]["response_diagnostics"]["status_code"], 400)

        # 真实 APIError 带有 httpx.Response fallback (code=0 时从 response.status_code 兜底提取)
        req = httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models")
        err_resp = genai_errors.APIError(0, {}, response=httpx.Response(403, request=req))
        self.assertEqual(extract_error_status_code(err_resp), 403)
        self.assertTrue(rejected_before_generation(err_resp))

    @unittest.skipUnless(genai_errors is not None, "google-genai 由宿主提供，未安装时跳过真实 SDK 异常契约")
    async def test_real_google_genai_server_error_503_logs_status_without_fallback(self):
        secret = "private gemini upstream error"
        err_503 = genai_errors.ServerError(503, {"error": {"code": 503, "message": secret, "status": "UNAVAILABLE"}})
        self.assertFalse(hasattr(err_503, "status_code"))
        self.assertEqual(extract_error_status_code(err_503), 503)
        self.assertFalse(rejected_before_generation(err_503))

        primary_503, _ = self._failing_provider(lambda: err_503, provider_id="primary")
        self.gateway_with(primary_503.local_provider(), self.provider("fallback"))
        self._enable_large_budget()
        with self.assertLogs("companion_v2.adapters.astrbot.provider", level="WARNING") as captured:
            with self.assertRaises(TaskError) as raised:
                await self.gateway.generate(self.model_run("real-genai-503"), self.request())
        self.assertEqual(raised.exception.code, "model_usage_unknown")
        self.assertEqual(self.called, [])
        logs_503 = "\n".join(captured.output)
        self.assertIn("status_code=503", logs_503)
        self.assertNotIn(secret, logs_503)
        calls_503 = self.gateway.observe(self.context, "real-genai-503").unwrap()["calls"]
        self.assertEqual(calls_503[0]["state"], "unknown")
        self.assertEqual(calls_503[0]["response_diagnostics"]["status_code"], 503)


if __name__ == "__main__":
    unittest.main()
