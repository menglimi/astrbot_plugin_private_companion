# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot_plugin_private_companion.helpers import _flat_get
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi
from astrbot_plugin_private_companion.reaction_asset_library import ReactionAssetLibrary


ROOT = Path(__file__).resolve().parents[1]
REACTION_SETTING_KEYS = {
    "reaction_expression_private_enabled",
    "reaction_expression_proactive_enabled",
    "reaction_expression_group_enabled",
    "reaction_expression_delivery_mode",
    "reaction_expression_image_format",
    "reaction_expression_trigger_probability",
    "reaction_expression_cooldown_seconds",
    "reaction_expression_semantic_trigger_enabled",
    "reaction_expression_low_latency_mode",
    "reaction_expression_candidate_limit",
}


class ReactionExpressionPageApiTests(unittest.TestCase):
    def test_feature_and_subsettings_are_exposed_by_page_contract(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None
        api.plugin = SimpleNamespace(enable_reaction_expression_experiment=True)
        api._screen_companion_available = lambda: False

        self.assertIn(
            "enable_reaction_expression_experiment",
            api._allowed_feature_keys(),
        )
        self.assertTrue(
            api._feature_flags()["enable_reaction_expression_experiment"]
        )
        self.assertTrue(REACTION_SETTING_KEYS <= api._allowed_setting_keys())

        settings = api._runtime_settings()
        self.assertTrue(REACTION_SETTING_KEYS <= set(settings))

    def test_runtime_summary_uses_real_counters_and_aggregates_recent_outcomes(self) -> None:
        plugin = SimpleNamespace(
            enable_reaction_expression_experiment=True,
            _reaction_expression_runtime={
                "attempts": "7",
                "offers": -2,
                "model_omissions": "3",
                "local_fallbacks": "2",
                "lookups": 4,
                "cache_hits": "2",
                "sent": 1,
                "skipped": 3,
                "last_reason": "cooldown\nignored",
                "last_latency_ms": -5,
                "total_lookup_ms": 123.456,
                "trigger_modes": {
                    "semantic_rule": "4",
                    "strong_emotion": 2,
                    "": 9,
                },
            },
        )
        api = PrivateCompanionPageApi(plugin)
        data = {
            "users": {
                "10001": {
                    "reaction_expression": {
                        "recent_outcomes": [
                            {"at": 10, "status": "sent", "reason": "delivered", "image_key": "private-a"},
                            {"at": 20, "status": "skipped", "reason": "cooldown", "image_key": "private-b"},
                            {"at": 30, "status": "skipped", "reason": "cooldown", "image_key": "private-c"},
                        ],
                        "preference": {"positive_count": 2, "negative_count": 1},
                    }
                },
                "10002": {
                    "reaction_expression": {
                        "recent_outcomes": [None],
                        "preference": {"positive_count": 1, "negative_count": 4},
                    }
                },
            }
        }

        summary = api._reaction_expression_runtime_summary(data)

        self.assertEqual(
            summary["runtime"],
            {
                "attempts": 7,
                "offers": 0,
                "model_omissions": 3,
                "local_fallbacks": 2,
                "lookups": 4,
                "cache_hits": 2,
                "sent": 1,
                "skipped": 3,
                "last_latency_ms": 0.0,
                "total_lookup_ms": 123.46,
                "trigger_modes": {
                    "semantic_rule": 4,
                    "strong_emotion": 2,
                },
                "last_reason": "cooldown ignored",
            },
        )
        self.assertEqual(summary["recent"]["tracked_user_count"], 2)
        self.assertEqual(summary["recent"]["attempt_count"], 3)
        self.assertEqual(summary["recent"]["sent_count"], 1)
        self.assertEqual(summary["recent"]["skipped_count"], 2)
        self.assertEqual(summary["recent"]["skip_reasons"], {"cooldown": 2})
        self.assertEqual(summary["recent"]["last_activity_at"], 30.0)
        self.assertEqual(summary["recent"]["positive_feedback_count"], 3)
        self.assertEqual(summary["recent"]["negative_feedback_count"], 5)
        self.assertNotIn("private-a", json.dumps(summary, ensure_ascii=False))


class ReactionExpressionPageApiSaveTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_update_normalizes_saves_and_returns_reaction_settings(self) -> None:
        schema = json.loads((ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
        payload = {
            "features": {"enable_reaction_expression_experiment": "true"},
            "settings": {
                "reaction_expression_private_enabled": "false",
                "reaction_expression_proactive_enabled": "false",
                "reaction_expression_group_enabled": "yes",
                "reaction_expression_delivery_mode": "separate_before",
                "reaction_expression_image_format": "qq_emoji",
                "reaction_expression_trigger_probability": 180,
                "reaction_expression_cooldown_seconds": 9999,
                "reaction_expression_semantic_trigger_enabled": "false",
                "reaction_expression_low_latency_mode": "off",
                "reaction_expression_candidate_limit": 99,
            },
        }
        expected = {
            "enable_reaction_expression_experiment": True,
            "reaction_expression_private_enabled": False,
            "reaction_expression_proactive_enabled": False,
            "reaction_expression_group_enabled": True,
            "reaction_expression_delivery_mode": "separate_before",
            "reaction_expression_image_format": "qq_emoji",
            "reaction_expression_trigger_probability": 1.0,
            "reaction_expression_cooldown_seconds": 3600,
            "reaction_expression_semantic_trigger_enabled": False,
            "reaction_expression_low_latency_mode": False,
            "reaction_expression_candidate_limit": 16,
        }

        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / "private_companion_config.json"
            config = AstrBotConfig(str(config_path), schema=schema)
            plugin = SimpleNamespace(config=config)
            api = PrivateCompanionPageApi(plugin)
            api.get_overview = AsyncMock(
                return_value={"success": True, "data": {"features": {}, "settings": {}}}
            )
            fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))

            with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
                result = await api.update_settings()

            self.assertTrue(result["success"])
            self.assertTrue(result["data"]["config_saved"])
            self.assertEqual(result["data"]["changed"], expected)
            self.assertTrue(
                result["data"]["features"]["enable_reaction_expression_experiment"]
            )
            for key in REACTION_SETTING_KEYS:
                self.assertEqual(result["data"]["settings"][key], expected[key], key)
                self.assertEqual(getattr(plugin, key), expected[key], key)

            reloaded = AstrBotConfig(str(config_path), schema=schema)
            for key, value in expected.items():
                self.assertEqual(_flat_get(reloaded, key), value, key)

    def test_delivery_mode_rejects_unknown_values(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None

        self.assertEqual(
            "separate_after",
            api._normalize_setting_value(
                "reaction_expression_delivery_mode",
                "unexpected-mode",
            ),
        )
        for mode in ("separate_after", "same_message", "separate_before"):
            self.assertEqual(
                mode,
                api._normalize_setting_value(
                    "reaction_expression_delivery_mode",
                    mode.upper(),
                ),
            )

    def test_image_format_rejects_unknown_values(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        api._schema_key_index_cache = None

        self.assertEqual(
            "image",
            api._normalize_setting_value(
                "reaction_expression_image_format",
                "unexpected-format",
            ),
        )
        for image_format in ("image", "qq_emoji"):
            self.assertEqual(
                image_format,
                api._normalize_setting_value(
                    "reaction_expression_image_format",
                    image_format.upper(),
                ),
            )


class ReactionLibraryPageApiTests(unittest.IsolatedAsyncioTestCase):
    PNG_BYTES = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    )

    async def test_import_list_update_preview_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plugin = SimpleNamespace(data_dir=folder)
            api = PrivateCompanionPageApi(plugin)
            encoded = base64.b64encode(self.PNG_BYTES).decode("ascii")
            import_request = SimpleNamespace(
                get_json=AsyncMock(
                    return_value={
                        "files": [{"name": "开心.png", "data": encoded}],
                        "metadata": {"emotions": ["开心"], "scopes": ["private"]},
                    }
                )
            )
            with patch("astrbot_plugin_private_companion.page_api.request", import_request):
                imported = await api.import_reaction_library()
            self.assertTrue(imported["success"])
            self.assertEqual(1, imported["data"]["imported"])
            item_id = imported["data"]["items"][0]["id"]

            list_request = SimpleNamespace(args={"q": "开心", "status": "enabled", "scope": "private", "page": "1", "page_size": "20"})
            with patch("astrbot_plugin_private_companion.page_api.request", list_request):
                listed = await api.list_reaction_library()
            self.assertEqual(1, listed["data"]["total"])
            self.assertEqual(item_id, listed["data"]["items"][0]["id"])

            update_request = SimpleNamespace(
                get_json=AsyncMock(return_value={"ids": [item_id], "changes": {"intents": "庆祝", "enabled": False}})
            )
            with patch("astrbot_plugin_private_companion.page_api.request", update_request):
                updated = await api.update_reaction_library()
            self.assertEqual(1, updated["data"]["updated"])

            preview_request = SimpleNamespace(args={"id": item_id})
            # `get_reaction_library_image_data` 住在 page_api_media mixin，其方法体里的
            # `request` 解析到该模块自己的全局（page_api_media.py:27），patch 宿主无效。
            with patch(
                "astrbot_plugin_private_companion.page_api_media.request",
                preview_request,
            ):
                preview = await api.get_reaction_library_image_data()
            self.assertTrue(preview["data"]["data_url"].startswith("data:image/png;base64,"))

            delete_request = SimpleNamespace(
                get_json=AsyncMock(return_value={"ids": [item_id], "confirm": True})
            )
            with patch("astrbot_plugin_private_companion.page_api.request", delete_request):
                deleted = await api.delete_reaction_library()
            self.assertEqual(1, deleted["data"]["deleted"])

    def test_analysis_parser_accepts_fenced_json_and_maps_indexes_to_ids(self) -> None:
        parsed = PrivateCompanionPageApi._parse_reaction_library_analysis(
            """```json
            [{"image_index": 1, "name": "无语摊手", "description": "角色摊手", "visible_text": "？", "tags": ["摊手"], "emotions": ["无语"], "intents": ["吐槽"]}]
            ```""",
            [{"id": "asset-a", "filename": "a.png"}],
        )

        self.assertEqual(1, len(parsed))
        self.assertEqual("asset-a", parsed[0]["id"])
        self.assertEqual(["吐槽"], parsed[0]["intents"])

    async def test_analysis_prefers_configured_plugin_vision_over_astrbot_caption(self) -> None:
        completion = json.dumps(
            [
                {
                    "image_index": 1,
                    "name": "插件模型识别",
                    "description": "插件识图模型完成表情识别",
                    "tags": ["插件识图"],
                    "emotions": ["开心"],
                    "intents": ["接梗"],
                }
            ],
            ensure_ascii=False,
        )
        plugin_provider = SimpleNamespace(
            text_chat=AsyncMock(
                return_value=SimpleNamespace(completion_text=completion)
            )
        )
        astrbot_provider = SimpleNamespace(
            text_chat=AsyncMock(
                return_value=SimpleNamespace(completion_text=completion)
            )
        )
        plugin = SimpleNamespace(plugin_vision_provider_id="plugin-vision")
        plugin._private_image_visual_provider_candidates = lambda _umo: [
            ("astrbot-vision", "astrbot_image_caption", ""),
            ("plugin-vision", "plugin_vision", ""),
        ]
        plugin._private_image_provider_by_id = lambda provider_id: {
            "plugin-vision": plugin_provider,
            "astrbot-vision": astrbot_provider,
        }.get(provider_id)
        plugin._provider_supports_image = lambda _provider: True
        plugin._private_image_provider_timeout_seconds = lambda *_args: 0.0
        plugin._can_run_llm_task = lambda *_args, **_kwargs: True
        api = PrivateCompanionPageApi(plugin)

        parsed, provider_id, error = await api._call_reaction_library_analysis_provider(
            [{"id": "asset-a", "filename": "a.png"}],
            ["data:image/png;base64,AA=="],
        )

        self.assertEqual("", error)
        self.assertEqual("plugin-vision", provider_id)
        self.assertEqual("插件模型识别", parsed[0]["name"])
        plugin_provider.text_chat.assert_awaited_once()
        astrbot_provider.text_chat.assert_not_awaited()

    async def test_background_analysis_uses_visual_provider_and_completes_item(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = ReactionAssetLibrary(folder)
            item = library.import_blobs([("001.png", self.PNG_BYTES)])["items"][0]
            provider = SimpleNamespace(
                text_chat=AsyncMock(
                    return_value=SimpleNamespace(
                        completion_text=json.dumps(
                            [
                                {
                                    "image_index": 1,
                                    "name": "开心点头",
                                    "description": "角色笑着点头",
                                    "visible_text": "好",
                                    "tags": ["点头"],
                                    "emotions": ["开心"],
                                    "intents": ["赞同"],
                                }
                            ],
                            ensure_ascii=False,
                        )
                    )
                )
            )
            plugin = SimpleNamespace(data_dir=folder)
            plugin._private_image_visual_provider_candidates = lambda _umo: [
                ("vision-test", "plugin_vision", "")
            ]
            plugin._private_image_provider_by_id = lambda provider_id: provider if provider_id == "vision-test" else None
            plugin._provider_supports_image = lambda _provider: True
            plugin._private_image_provider_timeout_seconds = lambda *_args: 0.0
            plugin._can_run_llm_task = lambda *_args, **_kwargs: True
            api = PrivateCompanionPageApi(plugin)

            await api._run_reaction_library_analysis_queue()

            analyzed = library.list_items()["items"][0]
            self.assertEqual(item["id"], analyzed["id"])
            self.assertEqual("complete", analyzed["analysis_status"])
            self.assertEqual("开心点头", analyzed["name"])
            self.assertEqual("vision-test", analyzed["analysis_provider"])
            provider.text_chat.assert_awaited_once()
            call_kwargs = provider.text_chat.await_args.kwargs
            self.assertEqual(1, len(call_kwargs["image_urls"]))
            self.assertIn("只输出一个 JSON 数组", call_kwargs["prompt"])

    async def test_background_analysis_skips_over_limit_primary_for_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = ReactionAssetLibrary(folder)
            item = library.import_blobs([("001.png", self.PNG_BYTES)])['items'][0]
            primary = SimpleNamespace(
                text_chat=AsyncMock(
                    return_value=SimpleNamespace(completion_text="主模型不应被调用")
                )
            )
            fallback = SimpleNamespace(
                text_chat=AsyncMock(
                    return_value=SimpleNamespace(
                        completion_text=json.dumps(
                            [{
                                "image_index": 1,
                                "name": "备用识别",
                                "description": "备用模型完成识别",
                                "tags": ["备用"],
                                "emotions": ["平静"],
                                "intents": ["接梗"],
                            }],
                            ensure_ascii=False,
                        )
                    )
                )
            )
            usage: list[dict] = []
            plugin = SimpleNamespace(data_dir=folder)
            plugin._private_image_visual_provider_candidates = lambda _umo: [
                ("vision-primary", "plugin_vision", ""),
                ("vision-fallback", "plugin_vision_fallback", ""),
            ]
            plugin._private_image_provider_by_id = lambda provider_id: {
                "vision-primary": primary,
                "vision-fallback": fallback,
            }.get(provider_id)
            plugin._provider_supports_image = lambda _provider: True
            plugin._private_image_provider_timeout_seconds = lambda *_args: 0.0
            plugin._can_run_llm_task = lambda *_args, **_kwargs: True
            plugin._model_token_limit_should_skip_primary = lambda **kwargs: kwargs.get("provider_id") == "vision-primary"
            plugin._record_llm_usage = lambda **kwargs: usage.append(kwargs)
            api = PrivateCompanionPageApi(plugin)

            await api._run_reaction_library_analysis_queue()

            analyzed = library.list_items()["items"][0]
            self.assertEqual("complete", analyzed["analysis_status"])
            self.assertEqual("vision-fallback", analyzed["analysis_provider"])
            primary.text_chat.assert_not_awaited()
            fallback.text_chat.assert_awaited_once()
            self.assertTrue(any(item.get("error") == "model_token_limit_exceeded" for item in usage))

    async def test_analyze_endpoint_requeues_completed_item(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = ReactionAssetLibrary(folder)
            item = library.import_blobs(
                [("停用自动识别.png", self.PNG_BYTES)],
                metadata={"auto_analyze": False},
            )["items"][0]
            plugin = SimpleNamespace(data_dir=folder)
            api = PrivateCompanionPageApi(plugin)
            api._schedule_reaction_library_analysis = lambda: None
            fake_request = SimpleNamespace(
                get_json=AsyncMock(return_value={"ids": [item["id"]], "force": True})
            )

            with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
                result = await api.analyze_reaction_library()

            self.assertTrue(result["success"])
            self.assertEqual(1, result["data"]["queued"])
            self.assertEqual("pending", library.list_items()["items"][0]["analysis_status"])


if __name__ == "__main__":
    unittest.main()
