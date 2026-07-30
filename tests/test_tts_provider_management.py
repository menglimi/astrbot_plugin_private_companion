# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _FakeProvider:
    def __init__(self, config: dict) -> None:
        self.provider_config = deepcopy(config)
        self.test = AsyncMock()


class _FakeProviderManager:
    def __init__(self, configs: list[dict], loaded: bool = True) -> None:
        self.providers_config = deepcopy(configs)
        self.inst_map = {
            item["id"]: _FakeProvider(item)
            for item in self.providers_config
            if loaded and item.get("enable")
        }
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []

    def get_merged_provider_config(self, config: dict) -> dict:
        return deepcopy(config)

    def get_provider_config_by_id(self, provider_id: str, **_kwargs):
        return next((deepcopy(item) for item in self.providers_config if item.get("id") == provider_id), None)

    async def create_provider(self, config: dict) -> None:
        self.created.append(deepcopy(config))
        self.providers_config.append(deepcopy(config))

    async def update_provider(self, provider_id: str, config: dict) -> None:
        self.updated.append((provider_id, deepcopy(config)))
        self.providers_config = [
            deepcopy(config) if item.get("id") == provider_id else item
            for item in self.providers_config
        ]
        if config.get("enable"):
            self.inst_map[provider_id] = _FakeProvider(config)
        else:
            self.inst_map.pop(provider_id, None)


def _fish_config(**overrides) -> dict:
    config = {
        "id": "fish-main",
        "type": "fishaudio_tts_api",
        "provider": "fishaudio",
        "provider_type": "text_to_speech",
        "enable": True,
        "api_key": "secret-api-key",
        "api_base": "https://api.fish.audio/v1",
        "proxy": "http://secret-proxy.local",
        "model": "s2.1-pro-free",
        "fishaudio-tts-character": "voice-a",
    }
    config.update(overrides)
    return config


class TtsProviderManagementTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.manager = _FakeProviderManager([_fish_config()])
        context = SimpleNamespace(
            provider_manager=self.manager,
            get_using_tts_provider=lambda: self.manager.inst_map.get("fish-main"),
        )
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        self.api.plugin = SimpleNamespace(context=context)

    def test_schema_includes_all_astrbot_tts_templates_and_fish_models(self) -> None:
        bundle = self.api._tts_provider_schema_bundle()
        self.assertGreaterEqual(len(bundle["templates"]), 13)
        fish = bundle["by_type"]["fishaudio_tts_api"]
        model = next(field for field in fish["fields"] if field["key"] == "model")
        self.assertEqual(
            [item["value"] for item in model["options"]],
            ["s2.1-pro-free", "s2.1-pro", "s2-pro", "s1"],
        )
        proxy = next(field for field in fish["fields"] if field["key"] == "proxy")
        self.assertEqual(proxy["label"], "代理地址")
        self.assertNotIn("provider_group.", proxy["hint"])

    def test_serialized_provider_never_returns_secret_values(self) -> None:
        payload = self.api._tts_provider_management_payload()
        item = payload["items"][0]
        self.assertEqual(item["values"]["api_key"], "")
        self.assertEqual(item["values"]["proxy"], "")
        self.assertTrue(item["secret_configured"]["api_key"])
        self.assertTrue(item["secret_configured"]["proxy"])
        self.assertNotIn("secret-api-key", repr(payload))
        self.assertNotIn("secret-proxy", repr(payload))

    def test_empty_secret_update_keeps_existing_values(self) -> None:
        normalized = self.api._normalized_tts_provider_update(
            _fish_config(),
            {"values": {"api_key": "", "proxy": "", "fishaudio-tts-character": "voice-b"}},
            self.api._tts_provider_schema_bundle(),
        )
        self.assertEqual(normalized["api_key"], "secret-api-key")
        self.assertEqual(normalized["proxy"], "http://secret-proxy.local")
        self.assertEqual(normalized["fishaudio-tts-character"], "voice-b")

    def test_invalid_fish_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Fish Audio 模型"):
            self.api._normalized_tts_provider_update(
                _fish_config(),
                {"values": {"model": "unknown-model"}},
                self.api._tts_provider_schema_bundle(),
            )

    async def test_create_provider_starts_disabled(self) -> None:
        fake_request = SimpleNamespace(
            get_json=AsyncMock(return_value={"type": "edge_tts", "id": "edge-ja"})
        )
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            result = await self.api.create_tts_provider_config()
        self.assertTrue(result["success"])
        self.assertEqual(self.manager.created[0]["id"], "edge-ja")
        self.assertFalse(self.manager.created[0]["enable"])

    async def test_update_uses_astrbot_provider_manager_and_preserves_secret(self) -> None:
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value={
            "provider_id": "fish-main",
            "config": {"enable": True, "values": {"api_key": "", "model": "s2-pro"}},
        }))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            result = await self.api.update_tts_provider_config()
        self.assertTrue(result["success"])
        _, updated = self.manager.updated[0]
        self.assertEqual(updated["api_key"], "secret-api-key")
        self.assertEqual(updated["model"], "s2-pro")

    async def test_language_clone_preserves_source_and_secrets(self) -> None:
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value={
            "source_provider_id": "fish-main",
            "language": "ja",
            "config": {
                "enable": True,
                "values": {"api_key": "", "proxy": "", "model": "s2.1-pro"},
            },
        }))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            result = await self.api.clone_tts_provider_config()
        self.assertTrue(result["success"])
        self.assertEqual(result["data"]["provider_id"], "fish-main-ja")
        cloned = self.manager.created[0]
        self.assertEqual(cloned["id"], "fish-main-ja")
        self.assertEqual(cloned["api_key"], "secret-api-key")
        self.assertEqual(cloned["proxy"], "http://secret-proxy.local")
        self.assertEqual(cloned["model"], "s2.1-pro")
        self.assertNotIn("secret-api-key", repr(result))
        self.assertNotIn("secret-proxy", repr(result))
        source = self.manager.get_provider_config_by_id("fish-main")
        self.assertEqual(source["model"], "s2.1-pro-free")

    def test_language_clone_id_remains_unique(self) -> None:
        clone_id = self.api._tts_language_clone_provider_id(
            "fish-main",
            "ja",
            {"fish-main", "fish-main-ja"},
        )
        self.assertEqual(clone_id, "fish-main-ja-2")

    async def test_disabled_provider_test_returns_clear_status(self) -> None:
        self.manager.providers_config = [_fish_config(enable=False)]
        self.manager.inst_map = {}
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value={"provider_id": "fish-main"}))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            result = await self.api.test_tts_provider_config()
        self.assertTrue(result["success"])
        self.assertFalse(result["data"]["ok"])
        self.assertIn("尚未启用或加载失败", result["data"]["error"])


if __name__ == "__main__":
    unittest.main()
