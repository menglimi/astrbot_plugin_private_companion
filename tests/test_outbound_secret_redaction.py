# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.helpers import _redact_outbound_secrets, _runtime_secret_values
from astrbot_plugin_private_companion.tts_tool_sanitizer import TtsToolSanitizerMixin


class OutboundSecretRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.owner = SimpleNamespace(
            external_image_api_key="custom-s" + "ecret-value-123456",
            backup_external_image_api_key="",
            balance_api_key="balance-" + "secret-654321",
            weather_api_key="",
            web_exploration_api_key="",
            external_image_api_endpoints=[{"api_key": "queue-secret-abcdef", "model": "senova-u1-fast"}],
            config={"provider": {"api_key": "provider-secret-xyz987"}},
        )

    def test_known_runtime_secrets_are_collected_without_unrelated_model_values(self) -> None:
        values = _runtime_secret_values(self.owner)
        self.assertIn("custom-secret-value-123456", values)
        self.assertIn("queue-secret-abcdef", values)
        self.assertIn("provider-secret-xyz987", values)
        self.assertNotIn("senova-u1-fast", values)

    def test_screenshot_style_sk_key_is_redacted_but_model_and_endpoint_remain(self) -> None:
        fake_key = "sk-" + "testonly00000000000000000000000000"
        text = f"{fake_key} 这是key，模型是senova-u1-fast，api地址是POST https://token.example/v1/images/generations"
        cleaned = _redact_outbound_secrets(text, self.owner)
        self.assertNotIn(fake_key, cleaned)
        self.assertIn("[密钥已隐藏]", cleaned)
        self.assertIn("senova-u1-fast", cleaned)
        self.assertIn("https://token.example/v1/images/generations", cleaned)

    def test_known_key_bearer_query_and_jwt_are_redacted(self) -> None:
        jwt = "eyJabcdefghijk.abcdefghijklmnop.abcdefghijklmnop"
        text = (
            "api_key=custom-s" "ecret-value-123456\n"
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
            "https://example.test/path?token=query-secret-123456\n"
            f"jwt={jwt}"
        )
        cleaned = _redact_outbound_secrets(text, self.owner)
        for secret in ("custom-secret-value-123456", "abcdefghijklmnopqrstuvwxyz", "query-secret-123456", jwt):
            self.assertNotIn(secret, cleaned)

    def test_normal_discussion_of_api_keys_is_not_destroyed(self) -> None:
        text = "这个 API Key 为什么返回 403？模型地址需要带 /v1 吗？"
        self.assertEqual(_redact_outbound_secrets(text, self.owner), text)

    def test_send_message_tool_plain_text_is_redacted(self) -> None:
        class Harness(TtsToolSanitizerMixin):
            external_image_api_key = "custom-s" + "ecret-value-123456"
            backup_external_image_api_key = ""
            balance_api_key = ""
            weather_api_key = ""
            web_exploration_api_key = ""
            external_image_api_endpoints = []
            config = {}

        messages = [{"type": "plain", "text": "key=custom-secret-value-123456，模型可用"}]
        cleaned = Harness()._clean_send_message_to_user_tool_messages(messages)
        self.assertNotIn("custom-secret-value-123456", cleaned[0]["text"])
        self.assertIn("模型可用", cleaned[0]["text"])

if __name__ == "__main__":
    unittest.main()
