"""Provider 的输出上限是「能力声明」，不是「任务默认预算」。

``model.max_output_tokens`` 决定一次请求默认要多长；Provider 的
``maximum_output_tokens`` 决定一份任务配置是否有效。两者共用同一个数时，
陪伴小屋里的任务上限就永远只能停在全局默认以下：抬高会被报成「超过所选
Provider 能力」，换 Provider 也没用（每个 Provider 都抄了同一个默认值），而
操作者从未给任何一个 Provider 设过上限。

未逐项声明上限的 Provider 因此取产品上限 ``MAX_MODEL_OUTPUT_TOKENS``；
``additional_providers[]`` 里的显式声明仍然是真正的按 Provider 上限，可以比
产品上限更小，也可以拒绝更大的任务预算。
"""

from types import SimpleNamespace
import unittest

from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial

from astrbot_host.model_settings import parse_routing
from astrbot_host.multi_settings import MultiHostSettings
from companion_v2.adapters.astrbot.provider import UsageTariff
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, assemble_providers
from companion_v2.model_gateway.models import MAX_MODEL_OUTPUT_TOKENS
from tests.multi_host_support import multi_config
from tests.segmentation_stream_support import native_provider


VERSION = "4.28.2"


def native_types():
    return SimpleNamespace(version=VERSION, provider=ProviderOpenAIOfficial,
        provider_types=(ProviderOpenAIOfficial,), video_provider_type=None,
        function_tool=FunctionTool, tool_set=ToolSet)


def context_for(*providers):
    by_id = {item.provider_config["id"]: item for item in providers}
    return SimpleNamespace(get_all_providers=lambda: list(providers),
                           get_provider_by_id=lambda provider_id: by_id.get(provider_id))


def budget_profile(tokens=2048):
    return TextProviderSettings("unused-primary", UsageTariff(0, 0, 0), tokens)


class ProviderOutputCeilingTests(unittest.TestCase):
    def test_an_undeclared_provider_ceiling_is_the_product_ceiling_not_the_default_budget(self):
        # 全局默认预算 2048 是「默认要多长」，不是「Provider 能生成多长」。
        adapters = assemble_providers(context_for(native_provider()), bridge=None,
            native_types=native_types(), primary_id="unused-primary", defaults=budget_profile())

        self.assertEqual([item.maximum_output_tokens for item in adapters], [MAX_MODEL_OUTPUT_TOKENS])
        self.assertGreater(MAX_MODEL_OUTPUT_TOKENS, 2048)

    def test_a_declared_per_provider_ceiling_still_wins(self):
        declared = TextProviderSettings("native-model", UsageTariff(0, 0, 0), 1024)

        adapters = assemble_providers(context_for(native_provider()), bridge=None,
            native_types=native_types(), primary_id="unused-primary", defaults=budget_profile(),
            additional=(declared,))

        self.assertEqual([item.maximum_output_tokens for item in adapters], [1024])

    def test_a_provider_profile_defaults_to_the_product_ceiling(self):
        self.assertEqual(TextProviderSettings("native-model", UsageTariff(0, 0, 0)).maximum_output_tokens,
                         MAX_MODEL_OUTPUT_TOKENS)

    def test_a_declared_ceiling_above_the_product_ceiling_is_refused(self):
        with self.assertRaises(ValueError):
            TextProviderSettings("native-model", UsageTariff(0, 0, 0), MAX_MODEL_OUTPUT_TOKENS + 1)

    def test_saved_routing_does_not_turn_the_default_budget_into_a_provider_limit(self):
        # 回复预算 2048 曾经让每个未声明的备用 Provider 都「只支持 2048」。
        providers, fallback, rules = parse_routing({
            "provider_id": "primary", "max_output_tokens": 2048,
            "additional_providers": [{"provider_id": "secondary"}]})

        self.assertEqual([item.maximum_output_tokens for item in providers], [MAX_MODEL_OUTPUT_TOKENS])
        self.assertEqual((fallback, rules), ("", "[]"))

    def test_saved_routing_keeps_rejecting_a_default_budget_above_a_declared_ceiling(self):
        with self.assertRaises(ValueError):
            parse_routing({
                "provider_id": "primary", "max_output_tokens": 2048, "fallback_provider_id": "secondary",
                "additional_providers": [{"provider_id": "secondary", "max_output_tokens": 1024}]})

    def test_the_default_budget_stays_a_cost_default(self):
        # 未配置时仍然是 2048：产品上限只放宽「能填多大」，不改默认成本。
        config = multi_config()
        config["model"].pop("max_output_tokens", None)

        self.assertEqual(MultiHostSettings.parse(config).common.max_output_tokens, 2048)


if __name__ == "__main__":
    unittest.main()
