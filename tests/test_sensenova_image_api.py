# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class SenseNovaHarness(ProactiveMessageMixin):
    external_image_api_platform = "auto"
    external_image_api_base_url = "https://token.sensenova.cn/v1"
    external_image_api_model = "senova-u1-fast"
    external_image_api_size = "1024x1024"


class SenseNovaImageApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = SenseNovaHarness()

    def test_auto_detects_sensenova_host_and_uses_official_endpoint(self) -> None:
        self.assertEqual(self.harness._resolved_external_image_api_platform(), "sensenova")
        self.assertEqual(
            self.harness._external_image_endpoint(),
            "https://token.sensenova.cn/v1/images/generations",
        )

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

    def test_legacy_model_typo_is_corrected_to_official_id(self) -> None:
        self.assertEqual(self.harness._sensenova_image_model(), "sensenova-u1-fast")
        self.assertEqual(self.harness._external_image_model_misconfiguration_note(), "")
        self.harness.external_image_api_model = "sensenova-chat"
        self.assertIn("sensenova-u1-fast", self.harness._external_image_model_misconfiguration_note())

    def test_common_sizes_map_to_official_2k_dimensions(self) -> None:
        self.assertEqual(self.harness._sanitize_sensenova_image_size("1024x1024"), "2048x2048")
        self.assertEqual(self.harness._sanitize_sensenova_image_size("1920x1080"), "2752x1536")
        self.assertEqual(self.harness._sanitize_sensenova_image_size("1080x1920"), "1536x2752")
        self.assertEqual(self.harness._sanitize_sensenova_image_size("1664x2496"), "1664x2496")

    def test_platform_specific_401_and_403_diagnostics_follow_official_docs(self) -> None:
        unauthorized = self.harness._external_image_api_error_note(401, '{"message":"Forbidden"}')
        unsupported_language = self.harness._external_image_api_error_note(403, '{"type":"permission_denied_error"}')
        self.assertIn("Key 无效或当前额度不足", unauthorized)
        self.assertIn("请求语言不受支持", unsupported_language)


if __name__ == "__main__":
    unittest.main()
