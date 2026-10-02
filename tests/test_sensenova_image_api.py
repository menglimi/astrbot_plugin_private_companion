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
        self.assertEqual(endpoint["model"], "senova-u1-fast")

    def test_configured_sensenova_model_id_is_preserved(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        for model in ("SenseNova U1.5 Fast", "custom-image-edit-model"):
            with self.subTest(model=model):
                endpoint = plugin._normalize_external_image_api_endpoint(
                    {
                        "platform": "sensenova",
                        "base_url": "https://token.sensenova.cn/v1",
                        "api_key": "test-key-placeholder",
                        "model": model,
                    }
                )
                self.assertEqual(endpoint["model"], model)


if __name__ == "__main__":
    unittest.main()
