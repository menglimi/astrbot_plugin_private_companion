# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class OpenRouterEndpointConfigTests(unittest.TestCase):
    def test_platform_aliases_and_endpoint_normalization_recognize_openrouter(self) -> None:
        for alias in ("openrouter", "OpenRouter", "open-router", "open_router", "openrouter.ai"):
            with self.subTest(alias=alias):
                self.assertEqual(
                    PrivateCompanionPlugin._normalize_external_image_api_platform(alias),
                    "openrouter",
                )

        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)
        endpoint = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "openai",
                "base_url": "https://openrouter.ai/api/v1/",
                "model": "x-ai/grok-imagine-image-quality",
            }
        )
        self.assertEqual(endpoint["platform"], "openrouter")


if __name__ == "__main__":
    unittest.main()
