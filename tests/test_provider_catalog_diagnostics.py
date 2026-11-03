"""A loaded provider the catalog refuses must say why, not only the type name.

``assemble_providers`` drops a provider it cannot wrap and writes one warning.
Until now that warning carried ``type(error).__name__`` only, which cannot
separate a configuration refusal from a defect: ``AstrBotTextProvider`` raises
``ValueError`` for a wrong native class, for an unusable streaming request and
for an overriding ``custom_extra_body``, and this module raises ``TaskError``
when the host swaps the provider mid-assembly. Those four causes need four
different responses, so the reason has to reach the log.
"""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from companion_v2.adapters.astrbot.provider import UsageTariff
from companion_v2.adapters.astrbot.provider_catalog import TextProviderSettings, TextVideoProvider, assemble_providers
from companion_v2.execution.models import ProviderBinding
from companion_v2.model_gateway.models import LocalProvider

LOGGER = "companion_v2.adapters.astrbot.provider_catalog"


class CatalogProvider:
    """The AstrBot text-provider surface plus every field the catalog reads."""

    def __init__(self, provider_id, *, provider_config=None):
        self.provider_id = provider_id
        self.provider_config = {"type": "openai_chat_completion", **(provider_config or {})}
        self.client = SimpleNamespace()
        self.default_params = {}

    def meta(self):
        return SimpleNamespace(id=self.provider_id)

    def get_model(self):
        return "catalog-model"

    async def _query(self, payloads, tools=None, **_):
        raise AssertionError("assembly must not perform a model request")


NATIVE_TYPES = SimpleNamespace(version="4.29.1", provider=CatalogProvider, video_provider_type=None,
                               provider_types=(CatalogProvider,), function_tool=object, tool_set=object)


def context(providers, *, missing=()):
    return SimpleNamespace(
        get_all_providers=lambda: list(providers),
        get_provider_by_id=lambda provider_id: None if provider_id in missing else next(
            (item for item in providers if item.provider_id == provider_id), None))


def assemble(providers, *, missing=()):
    return assemble_providers(context(providers, missing=missing), bridge=None,
        native_types=NATIVE_TYPES, primary_id="unused-primary",
        defaults=TextProviderSettings("unused-primary", UsageTariff(0, 0, 0)))


def detail_of(error):
    """The bounded reason helper, imported per call so the log path stays the subject."""
    from companion_v2.adapters.astrbot.provider_catalog import _unavailable_detail
    return _unavailable_detail(error)


def warning_lines(logs):
    return "\n".join(logs.output)


class CatalogUnavailableReasonTests(unittest.TestCase):
    def test_native_provider_registered_for_text_and_video_keeps_both_transports(self):
        native = CatalogProvider("gemini-native")
        video_calls = []
        async def text_generate(_envelope):
            return "text transport"

        async def video_generate(_envelope):
            return "native video transport"

        text = SimpleNamespace(native=native, provider_id="gemini-native", _current=lambda: None,
            reserve=lambda _envelope: "text reservation", generate=text_generate)
        video = SimpleNamespace(native=native, provider_id="gemini-native", _current=lambda: None,
            reserve=lambda _envelope: "video reservation", generate=video_generate)
        async def video_probe():
            video_calls.append("probe")

        video.probe = video_probe
        text.local_provider = lambda: LocalProvider(
            ProviderBinding("model.text", "gemini-native", "4.29.1", "generation"),
            ("text", "audio"), 8192, text.reserve, text.generate)
        video.local_provider = lambda: LocalProvider(
            ProviderBinding("model.native_video", "gemini-native", "4.29.1", "generation"),
            ("text", "tools", "image", "video"), 8192, video.reserve, video.generate)

        def video_factory(*args):
            video_calls.append(args)
            return video

        native_types = SimpleNamespace(
            version="4.29.1", provider=CatalogProvider, provider_types=(CatalogProvider,),
            is_text_provider=lambda item: isinstance(item, CatalogProvider),
            video_provider_type=CatalogProvider,
            video_provider_factory=video_factory,
            function_tool=object, tool_set=object,
        )
        with patch("companion_v2.adapters.astrbot.provider_catalog.AstrBotTextProvider",
                   return_value=text) as text_factory:
            adapters = assemble_providers(context([native]), bridge=None, native_types=native_types,
                primary_id="unused-primary",
                defaults=TextProviderSettings("unused-primary", UsageTariff(0, 0, 0)))

        self.assertEqual(len(adapters), 1)
        self.assertIsInstance(adapters[0], TextVideoProvider)
        self.assertEqual(adapters[0].local_provider().capabilities, ("text", "audio", "video"))
        self.assertEqual(len(video_calls), 1)
        self.assertIs(video_calls[0][3], native)
        self.assertEqual(text_factory.call_args.kwargs["binding"].capability, "model.text")
        ordinary = SimpleNamespace(original=SimpleNamespace(native_video_input=False, audios=(object(),)))
        original_video = SimpleNamespace(original=SimpleNamespace(native_video_input=True, audios=()))
        self.assertEqual(adapters[0].reserve(ordinary), "text reservation")
        self.assertEqual(adapters[0].reserve(original_video), "video reservation")
        self.assertEqual(asyncio.run(adapters[0].generate(ordinary)), "text transport")
        self.assertEqual(asyncio.run(adapters[0].generate(original_video)), "native video transport")
        asyncio.run(adapters[0].probe())
        self.assertEqual(video_calls[-1], "probe")

    def test_a_refused_provider_names_the_contract_it_violated(self):
        refused = CatalogProvider("relay/deepseek", provider_config={"custom_extra_body": {"tools": []}})
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            adapters = assemble([refused])

        self.assertEqual(adapters, ())
        line = warning_lines(logs)
        self.assertIn("relay/deepseek", line)
        self.assertIn("ValueError: custom provider parameters cannot override", line)

    def test_a_provider_swapped_during_assembly_reports_its_code(self):
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            adapters = assemble([CatalogProvider("swapped")], missing=("swapped",))

        self.assertEqual(adapters, ())
        self.assertIn("TaskError code=host_provider_changed", warning_lines(logs))

    def test_a_provider_without_a_readable_id_is_reported(self):
        class Anonymous(CatalogProvider):
            def meta(self):
                raise AttributeError("registry entry is not attached yet")

        with self.assertLogs(LOGGER, level="WARNING") as logs:
            adapters = assemble([Anonymous("unused")])

        self.assertEqual(adapters, ())
        line = warning_lines(logs)
        self.assertIn("no readable ID", line)
        # A host-side attribute failure carries host text; keep the type name only.
        self.assertIn("(AttributeError)", line)
        self.assertNotIn("registry entry is not attached yet", line)

    def test_one_refused_provider_does_not_hide_the_usable_ones(self):
        good = CatalogProvider("good")
        refused = CatalogProvider("refused", provider_config={"custom_extra_body": {"stream": True}})
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            adapters = assemble([good, refused])

        self.assertEqual([item.provider_id for item in adapters], ["good"])
        self.assertIn("refused", warning_lines(logs))

    def test_an_unrecognized_failure_keeps_the_type_name_only(self):
        self.assertEqual(detail_of(RuntimeError("carrier text")), "RuntimeError")
        self.assertEqual(detail_of(KeyError("carrier text")), "KeyError")

    def test_the_reported_reason_is_single_line_and_bounded(self):
        detail = detail_of(ValueError("first line\nsecond line " + "x" * 400))
        self.assertNotIn("\n", detail)
        self.assertTrue(detail.startswith("ValueError: first line second line "))
        self.assertLessEqual(len(detail), 200)


if __name__ == "__main__":
    unittest.main()
