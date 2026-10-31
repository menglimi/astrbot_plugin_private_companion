"""OpenAI-compatible provider subclasses must remain visible to V2.

AstrBot ships several OpenAI-compatible adapters as subclasses of
``ProviderOpenAIOfficial`` (Xiaomi, Groq, OpenRouter, ...). The provider is
still a verified native Provider; only its concrete class differs. Exact
``type(...) is`` checks used to hide those adapters from the model catalog and
from staging, which made the whole V2 page unusable with a Xiaomi provider.
"""

from types import SimpleNamespace
import unittest

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from companion_v2.adapters.astrbot.provider import AstrBotTextProvider, UsageTariff, provider_fingerprint
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers
from companion_v2.execution.models import ProviderBinding
from tests.segmentation_stream_support import native_provider


class XiaomiCompatibleProvider(ProviderOpenAIOfficial):
    """Stand-in for AstrBot's ``ProviderXiaomi`` adapter."""


def xiaomi_provider():
    provider = XiaomiCompatibleProvider.__new__(XiaomiCompatibleProvider)
    provider.__dict__.update(native_provider().__dict__)
    provider.provider_config = {**provider.provider_config, "type": "xiaomi_chat_completion"}
    # The real AstrBot registry supplies this; the test only needs a stable id.
    provider.meta = lambda: SimpleNamespace(id="native-model")
    return provider


class ProviderSubclassTests(unittest.TestCase):
    def test_openai_compatible_subclass_is_listed_by_the_text_catalog(self):
        provider = xiaomi_provider()
        context = SimpleNamespace(
            get_all_providers=lambda: [provider],
            get_provider_by_id=lambda provider_id: provider if provider_id == "native-model" else None,
            get_using_provider=lambda: provider,
        )
        # ``NativeAdapterTypes`` exposes ``provider_types`` as a property that always
        # carries the verified OpenAI class; a bare namespace without it made the
        # catalog look at a shape the real assembly never produces, so the case went
        # red on its own fixture instead of on a regression.
        native_types = SimpleNamespace(
            version="4.28.2",
            provider=ProviderOpenAIOfficial,
            provider_types=(ProviderOpenAIOfficial,),
            video_provider_type=None,
            function_tool=FunctionTool,
            tool_set=ToolSet,
        )

        adapters = assemble_providers(context, bridge=None, native_types=native_types,
            primary_id="unused-primary", defaults=TextProviderSettings("unused-primary", UsageTariff(0, 0, 0)))

        self.assertEqual([item.provider_id for item in adapters], ["native-model"])

    def test_openai_compatible_subclass_is_accepted_by_the_text_adapter(self):
        provider = xiaomi_provider()
        context = SimpleNamespace(get_provider_by_id=lambda provider_id: provider)

        adapter = AstrBotTextProvider(context, bridge=None,
            binding=ProviderBinding("model.text", "native-model", "4.28.2", provider_fingerprint(provider)),
            provider_id="native-model", version="4.28.2", provider_type=ProviderOpenAIOfficial,
            function_tool_type=FunctionTool, tool_set_type=ToolSet,
            tariff=UsageTariff(0, 0, 0), maximum_output_tokens=100, streaming=False)

        self.assertIs(adapter.native, provider)
        self.assertEqual(adapter.model, provider.get_model())


if __name__ == "__main__":
    unittest.main()
