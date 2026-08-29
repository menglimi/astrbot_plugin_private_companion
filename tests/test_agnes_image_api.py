# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class AgnesEndpointConfigTests(unittest.TestCase):
    def test_endpoint_normalization_preserves_agnes_ratio(self) -> None:
        plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

        endpoint = plugin._normalize_external_image_api_endpoint(
            {
                "platform": "auto",
                "base_url": "https://apihub.agnes-ai.com/v1/images/generations",
                "model": "agnes-image-2.1-flash",
                "ratio": "2:3",
            }
        )

        self.assertEqual(endpoint["platform"], "agnes")
        self.assertEqual(endpoint["ratio"], "2:3")


if __name__ == "__main__":
    unittest.main()
