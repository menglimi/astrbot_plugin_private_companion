"""Offline contract tests for the AstrBot Responses API task-model adapter."""

from dataclasses import replace
from datetime import datetime, timezone
from types import MappingProxyType, SimpleNamespace as NS
import json
import unittest
from unittest.mock import patch

from openai.types.responses import Response

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.sources.gemini_source import ProviderGoogleGenAI
from astrbot.core.provider.sources.openai_responses_source import ProviderOpenAIResponses
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from companion_v2.adapters.astrbot import provider as provider_module
from companion_v2.adapters.astrbot.model_wire import ModelWire, REPLY_CONTRACT_EXAMPLE, build_wire, tool_alias
from companion_v2.adapters.astrbot.provider import (
    AstrBotTextProvider, UsageTariff, _chat_image_urls, _responses_image_urls, provider_fingerprint,
)
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers
from companion_v2.conversation.foreground import PromptMaterial, PromptRequest, SealedForegroundContext
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.execution.models import ProviderBinding, digest
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, RoleBinding
from companion_v2.model_gateway.failures import ModelFailure
from companion_v2.model_gateway.models import Amount, ModelEnvelope, ToolSpec
from tests.native_adapter_support import NativeFixture


NOW = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
REPORTED_USAGE = Amount(40, 0, input_tokens=30, cached_input_tokens=10, output_tokens=10)


def _response(text=None, *, output=None, status="completed", incomplete_reason=None,
              response_id="resp-1", usage=True, error=None):
    data = {
        "id": response_id, "created_at": 0, "model": "gpt-test", "object": "response",
        "output": output if output is not None else [{
            "type": "message", "id": "msg-1", "status": "completed", "role": "assistant",
            "content": [{"type": "output_text", "text": text if text is not None else REPLY_CONTRACT_EXAMPLE,
                         "annotations": []}],
        }],
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [], "status": status,
    }
    if incomplete_reason is not None:
        data["incomplete_details"] = {"reason": incomplete_reason}
    if error is not None:
        data["error"] = error
    if usage:
        data["usage"] = {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40,
                         "input_tokens_details": {"cached_tokens": 10, "cache_write_tokens": 0},
                         "output_tokens_details": {"reasoning_tokens": 0}}
    return Response.model_validate(data)


def _function_call(name, arguments, *, call_id="call-1", item_id="fc-1"):
    return {"type": "function_call", "id": item_id, "call_id": call_id, "name": name,
            "arguments": arguments, "status": "completed"}


class _FakeState:
    def __init__(self):
        self.calls = []
        self.behavior = lambda **_: _response()


class _FakeResponses:
    def __init__(self, state):
        self.state = state

    async def create(self, **kwargs):
        self.state.calls.append(kwargs)
        return self.state.behavior(**kwargs)


class _FakeClient:
    def __init__(self, state, max_retries=2):
        self.state = state
        self.responses = _FakeResponses(state)
        self.max_retries = max_retries
        self.base_url = NS(host="api.example.invalid")

    def with_options(self, *, max_retries):
        return _FakeClient(self.state, max_retries)


class _Bridge:
    async def verify_role(self, *_args, **_kwargs):
        return None


def make_provider():
    native = ProviderOpenAIResponses({
        "id": "responses-task", "type": "openai_responses", "model": "gpt-test",
        "key": ["sk-test-not-a-real-key"], "api_base": "https://api.example.invalid/v1",
    }, {})
    state = _FakeState()
    native.client = _FakeClient(state)
    return native, state


def make_adapter(native, *, output_parameter="max_tokens", streaming=False):
    context = NS(get_provider_by_id=lambda provider_id: native if provider_id == "responses-task" else None)
    return AstrBotTextProvider(
        context, bridge=_Bridge(),
        binding=ProviderBinding("model.text", "responses-task", "4.27.3", provider_fingerprint(native)),
        provider_id="responses-task", version="4.27.3",
        provider_type=(ProviderOpenAIOfficial, ProviderGoogleGenAI),
        function_tool_type=FunctionTool, tool_set_type=ToolSet,
        tariff=UsageTariff(0, 0, 0), maximum_output_tokens=100,
        output_parameter=output_parameter, streaming=streaming,
    )


def make_envelope(*, output_format="conversation", response_format="text", response_schema_json=None,
                  streaming=None, streaming_mode="buffered", tools=(), on_reply_prefix=None):
    scope = ScopeRef("installation-1", "qq-main", ScopeKind.PRIVATE, "200",
                     bot_id="bot-a", account_id="100", conversation_id="conversation-1", session_id="200")
    principal = AuthenticatedHostPrincipal("installation-1", "host", "qq-main", "200",
                                           bot_id="bot-a", account_id="100")
    source = SourceRef("input-1", "conversation.host_input", scope, VersionRef("input-1", 1), NOW)
    event = InboundConversationEvent("event-1", principal, scope, source, "text", digest("你好"), NOW)
    role = RoleBinding(scope, SubjectRef("installation-1", SubjectKind.BOT, "role-1"), 1)
    request = PromptRequest(
        event, "你是原来的角色，比折的陪伴者。",
        SealedForegroundContext((PromptMaterial("conversation.host_input", "current_source", "你好", source),), (), ()),
        "只根据已见资料回复", role, output_format=output_format, response_format=response_format,
        response_schema_json=response_schema_json, streaming=streaming, streaming_mode=streaming_mode,
    )
    return ModelEnvelope(request, "任务提示", 100, "configuration-1", tools=tools,
                         before_dispatch=lambda: True, on_reply_prefix=on_reply_prefix,
                         reply_prefix_ready=lambda draft: True)


def media_wire(part):
    return ModelWire(
        messages=[{"role": "user", "content": [{"type": "text", "text": "附件"}, part]}],
        tools=(), aliases=MappingProxyType({}), evidence=MappingProxyType({}),
    )


class ResponsesProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_kind_transport_and_streaming_capability_are_explicit(self):
        native, _state = make_provider()
        adapter = make_adapter(native, streaming=True)

        self.assertEqual(adapter.kind, "openai_responses")
        self.assertEqual(adapter.local_provider().transport, "astrbot_openai_responses")
        self.assertFalse(adapter.streaming_supported)
        self.assertFalse(adapter.streaming)

    async def test_catalog_lists_responses_provider_with_its_own_transport(self):
        native, _state = make_provider()
        context = NS(
            get_all_providers=lambda: [native],
            get_provider_by_id=lambda provider_id: native if provider_id == "responses-task" else None,
        )
        native_types = NS(
            version="4.27.3", provider=ProviderOpenAIOfficial,
            text_provider_types=(ProviderGoogleGenAI,),
            provider_types=(ProviderOpenAIOfficial, ProviderGoogleGenAI),
            is_text_provider=lambda item: isinstance(item, (ProviderOpenAIOfficial, ProviderGoogleGenAI)),
            video_provider_type=None, function_tool=FunctionTool, tool_set=ToolSet,
        )
        adapters = assemble_providers(context, bridge=None, native_types=native_types,
                                      primary_id="unused-primary",
                                      defaults=TextProviderSettings("unused-primary", UsageTariff(0, 0, 0)))
        self.assertEqual([adapter.provider_id for adapter in adapters], ["responses-task"])
        self.assertEqual([adapter.kind for adapter in adapters], ["openai_responses"])
        self.assertEqual(adapters[0].local_provider().transport, "astrbot_openai_responses")

    async def test_incomplete_responses_surface_is_rejected_before_io(self):
        class IncompleteProvider:
            provider_config = {"id": "responses-task", "type": "openai_responses", "model": "gpt-test"}
            client = NS()

            def get_model(self): return "gpt-test"
            async def _query(self, *_args, **_kwargs): raise AssertionError("must not dispatch")

        with self.assertRaises(ValueError):
            AstrBotTextProvider(
                NS(get_provider_by_id=lambda _provider_id: IncompleteProvider()), bridge=_Bridge(),
                binding=ProviderBinding("model.text", "responses-task", "4.27.3", "fingerprint"),
                provider_id="responses-task", version="4.27.3", provider_type=(ProviderOpenAIOfficial,),
                function_tool_type=FunctionTool, tool_set_type=ToolSet,
                tariff=UsageTariff(0, 0, 0), maximum_output_tokens=100,
            )

    async def test_probe_uses_a_real_responses_payload(self):
        native, state = make_provider()
        adapter = make_adapter(native, output_parameter="max_completion_tokens")

        await adapter.probe()

        self.assertEqual(len(state.calls), 1)
        call = state.calls[0]
        self.assertEqual(call["input"], [{"type": "message", "role": "user", "content": "Reply OK."}])
        self.assertEqual(call["max_output_tokens"], 8)
        self.assertNotIn("messages", call)
        self.assertNotIn("max_tokens", call)
        self.assertNotIn("max_completion_tokens", call)
        self.assertNotIn("response_format", call)
        self.assertEqual(call.get("extra_body"), {})

    async def test_conversation_text_usage_and_receipt(self):
        native, state = make_provider()
        adapter = make_adapter(native)

        response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.draft.text, "我愿意陪你聊聊。")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertTrue(response.receipt_ref.endswith(":resp-1"))
        self.assertTrue(response.diagnostics.completion_present)
        self.assertEqual(response.diagnostics.parse_state, "validated")
        self.assertEqual(len(state.calls), 1)
        call = state.calls[0]
        self.assertIn("input", call)
        self.assertNotIn("messages", call)
        self.assertEqual(call["max_output_tokens"], 100)
        self.assertEqual(call.get("extra_body"), {})
        self.assertEqual(call["input"][0]["role"], "system")

    async def test_tool_call_is_decoded_from_strict_raw_arguments(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        tool = ToolSpec("memo.list", "Read memos", '{"type":"object","properties":{},"additionalProperties":false}')
        alias = tool_alias("memo.list")
        state.behavior = lambda **_: _response(output=[_function_call(alias, '{"limit":1}')])

        response = await adapter.generate(make_envelope(tools=(tool,)))

        self.assertEqual(response.status, "generated")
        self.assertEqual(len(response.tool_calls), 1)
        self.assertEqual(response.tool_calls[0].action, "memo.list")
        self.assertEqual(response.tool_calls[0].call_id, "call-1")
        self.assertEqual(json.loads(response.tool_calls[0].arguments_json), {"limit": 1})
        self.assertEqual(response.diagnostics.parse_state, "tools")

    async def test_malformed_tool_arguments_are_rejected_instead_of_repaired(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        tool = ToolSpec("memo.list", "Read memos", '{"type":"object"}')
        alias = tool_alias("memo.list")
        cases = (
            ("{bad", alias, "call-1", "fc-1"),
            ("[]", alias, "call-1", "fc-1"),
            ('{"limit":1,"limit":2}', alias, "call-1", "fc-1"),
            ("{}", "undeclared", "call-1", "fc-1"),
            ("{}", alias, "", "fc-1"),
        )
        for arguments, name, call_id, item_id in cases:
            with self.subTest(arguments=arguments, name=name, call_id=call_id, item_id=item_id):
                state.behavior = lambda **_: _response(output=[
                    _function_call(name, arguments, call_id=call_id, item_id=item_id)])
                response = await adapter.generate(make_envelope(tools=(tool,)))
                self.assertEqual(response.status, "unknown")
                self.assertEqual(response.tool_calls, ())
                self.assertEqual(response.failure, ModelFailure.WIRE_DECODE)

    async def test_duplicate_tool_ids_are_rejected(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        tool = ToolSpec("memo.list", "Read memos", '{"type":"object"}')
        alias = tool_alias("memo.list")
        state.behavior = lambda **_: _response(output=[
            _function_call(alias, "{}", call_id="call-1", item_id="fc-1"),
            _function_call(alias, "{}", call_id="call-1", item_id="fc-2"),
        ])

        response = await adapter.generate(make_envelope(tools=(tool,)))

        self.assertEqual(response.status, "unknown")
        self.assertEqual(response.tool_calls, ())
        self.assertEqual(response.failure, ModelFailure.WIRE_DECODE)

    async def test_memory_reflection_captures_the_normalized_response(self):
        native, _state = make_provider()
        adapter = make_adapter(native)
        native.client.state.behavior = lambda **_: _response('{"summary":"记得"}')

        response = await adapter.generate(make_envelope(output_format="memory_reflection"))

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.captured_output.text, '{"summary":"记得"}')
        self.assertEqual(response.captured_output.finish_reason, "stop")
        self.assertEqual(response.usage, REPORTED_USAGE)
        self.assertEqual(response.diagnostics.parse_state, "captured")

    async def test_role_state_json_object_uses_responses_text_format(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        state.behavior = lambda **_: _response('{"energy":1}')

        response = await adapter.generate(make_envelope(
            output_format="role_state", response_format="json_object"))

        self.assertEqual(response.status, "generated")
        call = state.calls[0]
        self.assertEqual(call["text"], {"format": {"type": "json_object"}})
        self.assertNotIn("response_format", call)
        self.assertEqual(call.get("extra_body"), {})

    async def test_json_schema_format_is_not_nested_under_json_schema_key(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        state.behavior = lambda **_: _response('{"summary":"x"}')
        envelope = make_envelope(
            output_format="memory_reflection", response_format="json_schema",
            response_schema_json='{"type":"object","properties":{}}')

        response = await adapter.generate(envelope)

        self.assertEqual(response.status, "generated")
        text = state.calls[0]["text"]
        self.assertEqual(text["format"]["type"], "json_schema")
        self.assertNotIn("json_schema", text["format"])
        self.assertEqual(text["format"]["schema"], {"type": "object", "properties": {}})
        self.assertTrue(text["format"]["strict"])

    async def test_max_completion_tokens_is_mapped_to_max_output_tokens(self):
        native, state = make_provider()
        adapter = make_adapter(native, output_parameter="max_completion_tokens")

        response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "generated")
        call = state.calls[0]
        self.assertEqual(call["max_output_tokens"], 100)
        self.assertNotIn("max_tokens", call)
        self.assertNotIn("max_completion_tokens", call)
        self.assertEqual(call.get("extra_body"), {})

    async def test_audio_and_video_inputs_fail_explicitly_before_dispatch(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        envelope = make_envelope()
        for part, expected in (
            ({"type": "input_audio", "input_audio": {"data": "AAAA", "format": "wav"}}, "audio"),
            ({"type": "video_data", "video_data": {"mime_type": "video/mp4", "data": "AAAA"}}, "video"),
        ):
            wire = media_wire(part)
            with self.subTest(kind=expected), patch.object(provider_module, "build_wire", lambda *a, **k: wire):
                response = await adapter.generate(envelope)
            self.assertEqual(response.status, "not_started")
            self.assertEqual(response.failure, ModelFailure.PREPARATION)
            self.assertEqual(response.diagnostics.error_code, "host_media_unsupported")
        self.assertEqual(state.calls, [])

    async def test_image_urls_survive_native_preparation(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA", "detail": "auto"}}
        wire = media_wire(image)

        with patch.object(provider_module, "build_wire", lambda *a, **k: wire):
            response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "generated")
        input_items = state.calls[0]["input"]
        self.assertEqual(_chat_image_urls(wire.messages), ("data:image/png;base64,AAAA",))
        self.assertEqual(_responses_image_urls(input_items), ("data:image/png;base64,AAAA",))
        self.assertIn("input_image", json.dumps(input_items))

    async def test_image_drop_by_native_preparation_is_rejected(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}
        wire = media_wire(image)

        async def dropping_prepare(*, prompt, contexts, model):
            return {"input": [{"type": "message", "role": "user", "content": "images dropped"}],
                    "model": model, "store": False}, []

        native._prepare_chat_payload = dropping_prepare
        with patch.object(provider_module, "build_wire", lambda *a, **k: wire):
            response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "not_started")
        self.assertEqual(response.failure, ModelFailure.PREPARATION)
        self.assertEqual(state.calls, [])

    async def test_cache_signature_uses_prepared_responses_input(self):
        native, _state = make_provider()
        adapter = make_adapter(native)
        signatures = []

        response = await adapter.generate(replace(make_envelope(), record_cache_signature=signatures.append))

        self.assertEqual(response.status, "generated")
        self.assertEqual(len(signatures), 1)
        self.assertGreater(signatures[0].message_count, 0)
        self.assertTrue(signatures[0].context_observed)

    async def test_status_failed_and_empty_output_are_native_parse_failures(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        for behavior in (
            lambda **_: _response(status="failed", output=[],
                                  error={"code": "server_error", "message": "provider failed"}),
            lambda **_: _response(output=[], usage=False),
        ):
            state.behavior = behavior
            response = await adapter.generate(make_envelope())
            self.assertEqual(response.status, "unknown")
            self.assertEqual(response.failure, ModelFailure.NATIVE_PARSE)
            self.assertIsNone(response.usage)
            self.assertEqual(response.diagnostics.parse_state, "native_rejected")

    async def test_content_filter_is_a_refusal_not_a_transport_failure(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        state.behavior = lambda **_: _response(status="incomplete", output=[], incomplete_reason="content_filter")

        response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.captured_output.finish_reason, "refusal")
        self.assertEqual(response.diagnostics.finish_reason, "content_filter")
        self.assertEqual(response.diagnostics.parse_state, "refusal")

    async def test_explicit_refusal_output_is_captured_as_refusal(self):
        native, state = make_provider()
        adapter = make_adapter(native)
        state.behavior = lambda **_: _response(output=[{
            "type": "message", "id": "msg-1", "status": "completed", "role": "assistant",
            "content": [{"type": "refusal", "refusal": "不能协助"}],
        }])

        response = await adapter.generate(make_envelope())

        self.assertEqual(response.status, "generated")
        self.assertEqual(response.captured_output.finish_reason, "refusal")
        self.assertEqual(response.captured_output.text, "")
        self.assertEqual(response.diagnostics.parse_state, "refusal")


class StagingStreamingReportTests(unittest.TestCase):
    def test_inspect_reads_the_adapter_streaming_capability(self):
        from companion_v2.adapters.astrbot.staging import AstrBotStagingSession

        native, _state = make_provider()
        adapter = make_adapter(native, streaming=True)
        session = object.__new__(AstrBotStagingSession)
        session.model = adapter
        session.profile = NS(streaming=True, target=NS(actor=NS()))
        session.profile_hash = "profile"
        session.review_only = True
        session._uses_local_sender = True
        session.model_profile = NS(group="model.test")
        session.config_target = NS()
        session.application = NS(state="new", tasks=NS(generation=0), now=lambda: NOW,
            configuration=NS(application_status=lambda *_args, **_kwargs:
                             NS(state="applied", generation=0)))
        session._configuration = lambda: NS(resolution_token="token")
        session._delivery_authorization_report = lambda: {"required": False}
        with patch("companion_v2.adapters.astrbot.staging.require", lambda value: value):
            report = session._inspect(None)
        self.assertEqual(report["model_transport"], "astrbot_openai_responses")
        self.assertFalse(report["streaming"]["available"])
        self.assertFalse(report["streaming"]["ready"])
        self.assertEqual(report["streaming"]["reason"], "streaming_transport_unavailable")


class ChatCompletionsRegressionTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    async def test_chat_completions_probe_and_generate_stay_unchanged(self):
        await self.native_model.probe()
        payload, _tools = self.native.provider.client.state.calls[-1]
        self.assertEqual(payload["max_tokens"], 8)
        self.assertNotIn("input", payload)

        response = await self.native_model.generate(await self.envelope())
        self.assertEqual(response.status, "generated")
        self.assertEqual(self.native_model.kind, "openai")
        self.assertEqual(self.native_model.local_provider().transport, "astrbot_openai")
        payload, _tools = self.native.provider.client.state.calls[-1]
        self.assertIn("messages", payload)
        self.assertEqual(payload["max_tokens"], 100)


if __name__ == "__main__":
    unittest.main()
