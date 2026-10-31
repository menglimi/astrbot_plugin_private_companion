"""Offline contract tests for the pinned native provider probe (F-01)."""

from copy import deepcopy
from types import SimpleNamespace as NS
import unittest

from tests.native_adapter_support import FakeClient, FakeProvider, NativeFixture, completion


class RecordingClient(FakeClient):
    """Record SDK kwargs without constructing an HTTP transport."""

    def __init__(self, state=None, max_retries=2):
        super().__init__(state, max_retries)
        self.chat = NS(completions=NS(create=self._create))

    def with_options(self, *, max_retries):
        return type(self)(self.state, max_retries)

    async def _create(self, **kwargs):
        self.state.http_calls.append(kwargs)
        return completion("OK")


class FixedStreamQueryProvider(FakeProvider):
    """Mirror AstrBot's non-streaming query and SDK keyword boundary.

    In particular, this is intentionally not the simplified legacy fake whose
    ``_query(payload, tools)`` accepts the duplicate stream field silently.
    """

    def __init__(self, *, sdk_supports_stream):
        super().__init__()
        self.sdk_supports_stream = sdk_supports_stream
        self.client = RecordingClient(self.client.state)
        self.client.state.http_calls = []
        if sdk_supports_stream:
            self.default_params = {*self.default_params, "stream"}

    async def _query(self, payloads, tools, *, request_max_retries=1, conversation_id=None):
        assert request_max_retries == 1 and self.client.max_retries == 0
        payloads = deepcopy(payloads)
        extra_body = {}
        for key in tuple(payloads):
            if key not in self.default_params:
                extra_body[key] = payloads.pop(key)
        custom_extra_body = self.provider_config.get("custom_extra_body", {})
        if isinstance(custom_extra_body, dict):
            extra_body.update(custom_extra_body)
        raw = await self.client.chat.completions.create(
            **payloads,
            stream=False,
            extra_body=extra_body,
            extra_headers={"conversation": conversation_id},
        )
        return await self._parse_openai_completion(raw, tools)


class ProviderProbeContractTests(NativeFixture, unittest.IsolatedAsyncioTestCase):
    def install_host(self, *, sdk_supports_stream):
        host = FixedStreamQueryProvider(sdk_supports_stream=sdk_supports_stream)
        self.native.provider = host
        self.native_model = self.make_model()
        return host

    async def test_host_double_reproduces_duplicate_stream_before_http(self):
        """Keep the pre-fix negative control: no network attempt is possible."""
        host = self.install_host(sdk_supports_stream=True)
        host.client = host.client.with_options(max_retries=0)
        payload = {"model": "fixture", "messages": [], "max_tokens": 8, "stream": False}

        with self.assertRaisesRegex(TypeError, "multiple values for keyword argument 'stream'"):
            await host._query(payload, None)

        self.assertEqual(host.client.state.http_calls, [])

    async def assert_probe_contract(self, *, sdk_supports_stream, output_parameter):
        host = self.install_host(sdk_supports_stream=sdk_supports_stream)
        provider = self.make_model(output_parameter=output_parameter)
        original_extra_body = deepcopy(host.provider_config["custom_extra_body"])

        await provider.probe()

        self.assertEqual(len(host.client.state.http_calls), 1)
        request = host.client.state.http_calls[0]
        self.assertIs(request["stream"], False)
        self.assertNotIn("stream", request["extra_body"])
        self.assertEqual(request["model"], "fixture")
        self.assertEqual(request["messages"], [{"role": "user", "content": "Reply OK."}])
        self.assertEqual(request[output_parameter], 256)
        other_parameter = "max_tokens" if output_parameter == "max_completion_tokens" else "max_completion_tokens"
        self.assertNotIn(other_parameter, request)
        self.assertEqual(request["extra_body"], original_extra_body)
        self.assertEqual(request["extra_body"], {"temperature": 0.7})
        self.assertNotIn("extra_body", request["extra_body"])
        self.assertEqual(host.provider_config["custom_extra_body"], original_extra_body)
        self.assertIs(self.native.provider, host)
        self.assertEqual(provider.output_parameter, output_parameter)

    async def test_probe_leaves_non_streaming_to_host_when_sdk_recognizes_stream(self):
        """Before F-01 this raises duplicate-stream TypeError before HTTP."""
        await self.assert_probe_contract(sdk_supports_stream=True, output_parameter="max_tokens")

    async def test_probe_contract_when_host_moves_unknown_fields_to_extra_body(self):
        """A reduced fake must not hide payload ownership or extra_body semantics."""
        await self.assert_probe_contract(
            sdk_supports_stream=False,
            output_parameter="max_completion_tokens",
        )
