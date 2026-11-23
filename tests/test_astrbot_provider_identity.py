"""宿主刷新 Provider 注册表后的身份复核：等价重建续接，真实变化拒绝。"""

from types import SimpleNamespace as NS
import unittest

from companion_v2.adapters.astrbot.provider import AstrBotTextProvider, UsageTariff
from companion_v2.execution.models import ProviderBinding, TaskError


class StubProvider:
    """只实现 AstrBot 文本 Provider 对 V2 暴露的表面。"""

    def __init__(self):
        self.provider_config = {"id": "native", "type": "googlegenai_chat_completion",
                                "model": "gemini-x", "key": ["secret-value"]}
        self.client = object()
        self.default_params = {"temperature": 1.0}

    def get_model(self):
        return self.provider_config.get("model", "")

    async def _query(self, payload, tools, *, request_max_retries=None):
        return NS(completion_text="ok")


class StubContext:
    def __init__(self, provider):
        self.provider = provider

    def get_provider_by_id(self, provider_id):
        return self.provider


def clone_provider_class(name):
    """模拟 AstrBot 重新导入适配器模块后得到的同名新类。"""
    body = {key: value for key, value in StubProvider.__dict__.items()
            if key not in ("__dict__", "__weakref__", "__module__", "__qualname__", "__doc__")}
    return type(name, (), body)


def make_provider(native=None):
    native = native or StubProvider()
    provider = AstrBotTextProvider(
        StubContext(native), bridge=NS(),
        binding=ProviderBinding("model.text", "native", "4.29.1", "generation-1"),
        provider_id="native", version="4.29.1", provider_type=(StubProvider,),
        function_tool_type=object, tool_set_type=object, tariff=UsageTariff(3, 1, 9),
        maximum_output_tokens=1024)
    return provider, native


class ProviderIdentityTests(unittest.TestCase):
    def test_host_added_fields_do_not_replace_the_provider(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.provider_config["modalities"] = []
        replacement.provider_config["reasoning"] = False
        provider.context.provider = replacement

        provider._current()

        self.assertIs(provider.native, replacement)
        self.assertIs(provider.client, replacement.client)

    def test_host_rewritten_modalities_do_not_replace_the_provider(self):
        native = StubProvider()
        native.provider_config["modalities"] = []
        provider, _native = make_provider(native)
        replacement = StubProvider()
        replacement.provider_config["modalities"] = ["text", "tool_use", "image"]
        provider.context.provider = replacement

        provider._current()

        self.assertIs(provider.native, replacement)
        self.assertIs(provider.client, replacement.client)

    def test_reimported_host_class_is_the_same_provider(self):
        reimported = clone_provider_class("StubProvider")
        provider, _native = make_provider()
        replacement = reimported()
        provider.context.provider = replacement

        provider._current()

        self.assertIs(provider.native, replacement)

    def test_unrelated_host_class_is_rejected(self):
        unrelated = clone_provider_class("OtherProvider")
        provider, _native = make_provider()
        replacement = unrelated()
        replacement.provider_config["type"] = "openai_chat_completion"
        provider.context.provider = replacement

        with self.assertRaises(TaskError) as raised:
            provider._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")

    def test_refreshed_credential_is_rebound_without_recording_its_value(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.provider_config["key"] = ["another-secret"]
        provider.context.provider = replacement

        provider._current()

        self.assertIs(provider.native, replacement)
        self.assertEqual(provider.config["key"], ["another-secret"])
        self.assertNotIn("another-secret", str(provider.binding))

    def test_credential_refresh_does_not_hide_another_changed_field(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.provider_config["key"] = ["another-secret"]
        replacement.provider_config["model"] = "gemini-y"
        provider.context.provider = replacement

        with self.assertRaises(TaskError) as raised:
            provider._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertNotIn("another-secret", str(raised.exception))

    def test_removed_captured_field_is_rejected(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.provider_config.pop("key")
        provider.context.provider = replacement

        with self.assertRaises(TaskError) as raised:
            provider._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")
        self.assertIn("key", str(raised.exception))

    def test_changed_model_is_rejected(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.provider_config["model"] = "gemini-y"
        provider.context.provider = replacement

        with self.assertRaises(TaskError) as raised:
            provider._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")

    def test_changed_default_parameters_are_rejected(self):
        provider, _native = make_provider()
        replacement = StubProvider()
        replacement.default_params = {"top_p": 0.2}
        provider.context.provider = replacement

        with self.assertRaises(TaskError) as raised:
            provider._current()

        self.assertEqual(raised.exception.code, "host_provider_changed")


class GeminiStructuredJsonMimeTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _envelope(*, tools=()):
        import json
        from datetime import datetime, timezone
        from companion_v2.conversation.content.authoring import CREATE_SCHEMA
        from companion_v2.conversation.foreground import (
            BackgroundPromptTrigger, PromptRequest, SealedForegroundContext,
        )
        from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
        from companion_v2.identity_policy.models import AuthenticatedHostPrincipal, RoleBinding
        from companion_v2.model_gateway.models import ModelEnvelope

        scope = ScopeRef("inst-1", "onebot", ScopeKind.PRIVATE, "u-1", "bot-1", "acc-1", "c-1", "s-1")
        principal = AuthenticatedHostPrincipal("inst-1", "host-1", "onebot", "u-1", "bot-1", "acc-1")
        role = RoleBinding(scope, SubjectRef("inst-1", SubjectKind.BOT, "role-1"), 1)
        trigger = BackgroundPromptTrigger("req-1", principal, scope, datetime(2026, 11, 22, tzinfo=timezone.utc))
        req = PromptRequest(
            event=trigger,
            role_instruction="role instruction",
            context=SealedForegroundContext((), (), ()),
            instruction="write a short story",
            role_binding=role,
            output_format="content_authoring",
            response_format="json_schema",
            response_schema_json=json.dumps(CREATE_SCHEMA),
        )
        return ModelEnvelope(
            original=req,
            task_prompt="task prompt",
            max_output_tokens=2048,
            configuration_token="cfg-1",
            tools=tools,
            before_dispatch=lambda: None,
        )

    async def test_gemini_structured_json_sets_response_mime_type_on_config_and_skips_when_config_has_tools(self):
        captured_payloads = []
        captured_configs = []
        builtin_tools = [None]

        class FakeGeminiProvider(StubProvider):
            async def _prepare_query_config(self, payloads, tools, tool_choice, system_instruction, modalities):
                return NS(response_mime_type=None, tools=builtin_tools[0] or tools)

            async def _query(self, payload, tools, *, request_max_retries=None):
                captured_payloads.append(dict(payload))
                cfg = await self._prepare_query_config(payload, tools, "auto", None, ["TEXT"])
                captured_configs.append(cfg)
                return NS(
                    role="assistant",
                    completion_text='{"title":"夜航","work_type":"短篇","body":"正文"}',
                    tools_call_name=[],
                    tools_call_args=[],
                    tools_call_ids=[],
                    raw_completion=None,
                )

        async def _verify_role(*_args, **_kwargs):
            return None

        native = FakeGeminiProvider()
        provider = AstrBotTextProvider(
            StubContext(native), bridge=NS(verify_role=_verify_role),
            binding=ProviderBinding("model.text", "native", "4.29.4", "generation-1"),
            provider_id="native", version="4.29.4", provider_type=(FakeGeminiProvider,),
            function_tool_type=lambda **kw: NS(**kw), tool_set_type=lambda tools: NS(tools=tools),
            tariff=UsageTariff(3, 1, 9), maximum_output_tokens=4096,
        )
        self.assertTrue(provider.supports_gemini_json_mime)
        self.assertFalse(provider.supports_response_format)

        result = await provider.generate(self._envelope())
        self.assertEqual(result.status, "generated")
        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[-1].get("response_mime_type"), "application/json")
        self.assertEqual(captured_configs[-1].response_mime_type, "application/json")

        # When Gemini built-in tools (e.g. Google Search) populate config.tools,
        # response_mime_type must not be set on GenerateContentConfig.
        builtin_tools[0] = [NS(google_search=True)]
        result_with_tools = await provider.generate(self._envelope())
        self.assertEqual(result_with_tools.status, "generated")
        self.assertEqual(len(captured_configs), 2)
        self.assertIsNone(captured_configs[-1].response_mime_type)

    async def test_gemini_retries_once_without_response_mime_type_when_relay_rejects_it(self):
        attempts = []

        class RejectMimeOnceError(RuntimeError):
            status_code = 400

        class RelayGeminiProvider(StubProvider):
            async def _prepare_query_config(self, payloads, tools, tool_choice, system_instruction, modalities):
                return NS(response_mime_type=None, tools=None)

            async def _query(self, payload, tools, *, request_max_retries=None):
                cfg = await self._prepare_query_config(payload, tools, "auto", None, ["TEXT"])
                attempts.append((payload.get("response_mime_type"), cfg.response_mime_type))
                if payload.get("response_mime_type"):
                    raise RejectMimeOnceError("400 Invalid argument: response_mime_type unsupported")
                return NS(
                    role="assistant",
                    completion_text='{"title":"夜航","work_type":"短篇","body":"正文"}',
                    tools_call_name=[],
                    tools_call_args=[],
                    tools_call_ids=[],
                    raw_completion=None,
                )

        async def _verify_role(*_args, **_kwargs):
            return None

        native = RelayGeminiProvider()
        provider = AstrBotTextProvider(
            StubContext(native), bridge=NS(verify_role=_verify_role),
            binding=ProviderBinding("model.text", "native", "4.29.4", "generation-1"),
            provider_id="native", version="4.29.4", provider_type=(RelayGeminiProvider,),
            function_tool_type=lambda **kw: NS(**kw), tool_set_type=lambda tools: NS(tools=tools),
            tariff=UsageTariff(3, 1, 9), maximum_output_tokens=4096,
        )
        result = await provider.generate(self._envelope())
        self.assertEqual(result.status, "generated")
        self.assertEqual(attempts, [("application/json", "application/json"), (None, None)])


if __name__ == "__main__":
    unittest.main()
