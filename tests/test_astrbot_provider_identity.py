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


if __name__ == "__main__":
    unittest.main()
