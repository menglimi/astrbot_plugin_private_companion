# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class MiniMaxEndpointConfigTests(unittest.TestCase):
    def test_endpoint_queue_identifies_official_hosts_without_runtime_rewrite(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        mainland = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "auto",
                "base_url": "https://api.minimaxi.com/v1/image/generation",
                "model": "image-01",
            }
        )
        international = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "auto",
                "base_url": "https://api.minimax.io/v1/images/generations",
                "model": "image-01",
            }
        )

        self.assertEqual(mainland["platform"], "minimax")
        self.assertEqual(mainland["base_url"], "https://api.minimaxi.com/v1/image/generation")
        self.assertEqual(international["platform"], "minimax")
        self.assertEqual(international["base_url"], "https://api.minimax.io/v1/images/generations")

    def test_explicit_openai_proxy_is_not_reclassified_by_model_name(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        endpoint = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "openai",
                "base_url": "https://image-proxy.example/v1",
                "model": "image-01-live",
            }
        )

        self.assertEqual(endpoint["platform"], "openai")


if __name__ == "__main__":
    unittest.main()
