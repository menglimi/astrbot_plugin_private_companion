# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class SenseNovaEndpointConfigTests(unittest.TestCase):
    def test_endpoint_config_is_normalized_when_saved(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        endpoint = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "auto",
                "base_url": "https://token.sensenova.cn/v1",
                "api_key": "test-key-placeholder",
                "model": "senova-u1-fast",
            }
        )

        self.assertEqual(endpoint["platform"], "sensenova")
        self.assertEqual(endpoint["model"], "sensenova-u1-fast")


if __name__ == "__main__":
    unittest.main()
