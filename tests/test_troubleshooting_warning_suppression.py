# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from quart import Quart

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class TroubleshootingWarningSuppressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)

    def test_warning_type_is_stable_and_scope_sensitive(self) -> None:
        first = self.api._troubleshooting_warning_type("check", "主动循环心跳不新鲜")
        second = self.api._troubleshooting_warning_type("check", "主动循环心跳不新鲜")
        event = self.api._troubleshooting_warning_type("event", "主动循环心跳不新鲜")

        self.assertEqual(first, second)
        self.assertRegex(first, r"^warning:[0-9a-f]{20}$")
        self.assertNotEqual(first, event)

    def test_semantic_warning_type_is_shared_across_surfaces(self) -> None:
        diagnostic = self.api._troubleshooting_diagnostics_with_types(
            [
                {
                    "level": "warn",
                    "title": "TTS 配置已开但 provider 不可用",
                    "warning_code": "tts.provider_unavailable",
                }
            ]
        )[0]
        check_type = self.api._troubleshooting_semantic_warning_type("tts.provider_unavailable")

        self.assertEqual(diagnostic["warning_type"], check_type)

    def test_proactive_warning_types_are_split_by_reason_and_action(self) -> None:
        item = {"action": "photo_text", "reason": "candidate_failed"}
        timeout = self.api._troubleshooting_proactive_warning_code("candidate", item, "生图等待超时")
        provider = self.api._troubleshooting_proactive_warning_code("candidate", item, "图片模型 provider 不可用")
        send = self.api._troubleshooting_proactive_warning_code("candidate", item, "图片发送失败")

        self.assertEqual(timeout, "proactive.candidate.timeout.photo_text")
        self.assertEqual(provider, "proactive.candidate.provider.photo_text")
        self.assertEqual(send, "proactive.candidate.send.photo_text")

    def test_persona_routing_warning_is_rendered_with_semantic_code(self) -> None:
        self.api.plugin = SimpleNamespace(
            _format_timestamp_elapsed=lambda _ts: "刚刚"
        )
        events = self.api._troubleshooting_recent_events(
            diagnostics=[],
            proactive_tasks={},
            proactive_candidates={},
            token_stats={"recent": []},
            persona_routing_warnings=[
                {
                    "code": "persona.route.passive_primary_fallback",
                    "level": "warn",
                    "disposition": "fallback",
                    "reason_code": "persona_profile_missing",
                    "requested_persona_id": "alt",
                    "active_persona_id": "main",
                    "window_key": "QBot123:GroupMessage:opaque",
                    "last_ts": 1,
                    "count": 3,
                }
            ],
        )

        item = events[0]
        self.assertEqual("人格路由", item["source"])
        self.assertEqual("被动消息已回退主人格", item["title"])
        self.assertEqual(
            "persona.route.passive_primary_fallback", item["warning_code"]
        )
        self.assertEqual(
            self.api._troubleshooting_semantic_warning_type(item["warning_code"]),
            item["warning_type"],
        )

    def test_unspecified_plugin_persona_has_dedicated_title(self) -> None:
        self.api.plugin = SimpleNamespace(
            _format_timestamp_elapsed=lambda _ts: "刚刚"
        )
        events = self.api._troubleshooting_recent_events(
            diagnostics=[],
            proactive_tasks={},
            proactive_candidates={},
            token_stats={"recent": []},
            persona_routing_warnings=[
                {
                    "code": "persona.route.plugin_persona_unspecified",
                    "level": "warn",
                    "disposition": "sent_with_warning",
                    "reason_code": "plugin_persona_unspecified",
                    "window_key": "onebot:FriendMessage:1",
                    "last_ts": 1,
                    "count": 1,
                }
            ],
        )

        self.assertEqual("插件人格未指定", events[0]["title"])
        self.assertEqual(
            "persona.route.plugin_persona_unspecified", events[0]["warning_code"]
        )

    def test_only_warning_level_items_are_suppressed(self) -> None:
        key = self.api._troubleshooting_warning_type("check", "测试警告")
        items = [
            {"level": "warn", "title": "测试警告", "warning_type": key},
            {"level": "error", "title": "同类型错误", "warning_type": key},
            {"level": "info", "title": "同类型信息", "warning_type": key},
        ]

        visible = self.api._filter_suppressed_troubleshooting_warnings(items, {key})

        self.assertEqual([item["level"] for item in visible], ["error", "info"])

    def test_saved_records_are_normalized_and_deduplicated(self) -> None:
        key = self.api._troubleshooting_warning_type("diagnostic", "TTS 配置警告")
        records = self.api._troubleshooting_warning_records(
            {
                "troubleshooting_suppressed_warning_types": [
                    {"key": key, "title": "TTS 配置警告", "source": "运行诊断", "suppressed_at": 10},
                    {"key": key, "title": "重复项"},
                    {"key": "invalid", "title": "无效项"},
                ]
            }
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "TTS 配置警告")
        self.assertEqual(records[0]["source"], "运行诊断")

    def test_suppression_payload_counts_current_warning_occurrences(self) -> None:
        key = self.api._troubleshooting_warning_type("event", "proactive_audit")
        records = [{"key": key, "title": "主动审计", "source": "最近问题", "suppressed_at": 10}]
        payload = self.api._troubleshooting_suppression_payload(
            records,
            [
                {"level": "warn", "warning_type": key},
                {"level": "warn", "warning_type": key},
                {"level": "error", "warning_type": key},
            ],
        )

        self.assertEqual(payload[0]["current_count"], 2)

    def test_chain_warning_items_are_independently_suppressible(self) -> None:
        timeout_text = "估算完整回退链路约 300s，排障测试外层最多等待 240s；极端慢链路仍可能被测试层截断。"
        scope_text = "排障生图只检查生成文件，不覆盖后续发图。"
        timeout_code = self.api._troubleshooting_chain_warning_code("image_generation_text2img", timeout_text)
        timeout_key = self.api._troubleshooting_semantic_warning_type(timeout_code)

        visible, all_items = self.api._troubleshooting_chain_tests_with_warning_items(
            {"image_generation_text2img": {"warnings": [timeout_text, scope_text]}},
            {timeout_key},
        )

        result = visible["image_generation_text2img"]
        self.assertEqual(len(all_items), 2)
        self.assertEqual(len(result["warning_items"]), 1)
        self.assertEqual(result["warning_items"][0]["warning_code"], "chain.image_generation_text2img.test_scope")

    def test_same_runtime_condition_uses_one_type_across_check_and_diagnostic(self) -> None:
        self.api.plugin = SimpleNamespace(
            max_daily_messages=2,
            enable_photo_text_action=True,
            _photo_text_available=lambda *args, **kwargs: True,
        )
        diagnostics = self.api._troubleshooting_diagnostics_with_types(
            [
                {
                    "level": "warn",
                    "title": "TTS 配置已开但 provider 不可用",
                    "warning_code": "tts.provider_unavailable",
                }
            ]
        )
        checks = self.api._troubleshooting_checks(
            data={},
            users={"1": {"enabled": True}},
            groups={},
            diagnostics=diagnostics,
            proactive_tasks={"runtime": {"healthy": True, "last_tick_started": "刚刚"}},
            proactive_candidates={"items": []},
            token_stats={"budget": {}, "recent": []},
            cache={
                "private_image_vision": {
                    "enabled": True,
                    "provider_runtime": {
                        "candidates": [{"available": True, "supports_image": True, "cooldown": False}],
                    },
                }
            },
            tts={"enhancement_enabled": True, "provider_available": False},
            sqlite_status={"items": [{"level": "warn", "text": "journal_mode 不是 WAL"}]},
        )

        tts_check = next(item for item in checks if item["warning_code"] == "tts.provider_unavailable")
        sqlite_check = next(item for item in checks if item["warning_code"] == "sqlite.wal")
        self.assertEqual(tts_check["warning_type"], diagnostics[0]["warning_type"])
        self.assertEqual(sqlite_check["warning_type"], self.api._troubleshooting_semantic_warning_type("sqlite.wal"))

    def test_unlimited_photo_scope_is_not_reported_as_unavailable_quota(self) -> None:
        self.api.plugin = SimpleNamespace(
            max_daily_messages=2,
            enable_photo_text_action=True,
            photo_generation_proactive_max_daily=-1,
            _photo_generation_scope_daily_limit=lambda _scope: -1,
            _photo_text_available=lambda *args, **kwargs: False,
        )

        checks = self.api._troubleshooting_checks(
            data={},
            users={"1": {"enabled": True}},
            groups={},
            diagnostics=[],
            proactive_tasks={"runtime": {"healthy": True, "last_tick_started": "刚刚"}},
            proactive_candidates={"items": []},
            token_stats={"budget": {}, "recent": []},
            cache={"private_image_vision": {"enabled": False}},
            tts={"enhancement_enabled": False},
            sqlite_status={"items": []},
        )

        photo_check = next(item for item in checks if item["warning_code"] == "image.proactive_backend_unavailable")
        self.assertEqual("主动带图当前不可用", photo_check["title"])
        self.assertIn("不限量（-1）", photo_check["text"])
        self.assertIn("不是该范围额度耗尽", photo_check["text"])


class TroubleshootingWarningSuppressionEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        class PluginStub:
            def __init__(self) -> None:
                self.data: dict = {}
                self._data_lock = asyncio.Lock()
                self.save_count = 0

            def _save_data_sync(self, **_kwargs) -> None:
                self.save_count += 1

        self.app = Quart(__name__)
        self.api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)
        self.api.plugin = PluginStub()

    async def _post(self, payload: dict) -> dict:
        async with self.app.test_request_context("/", method="POST", json=payload):
            return await self.api.update_troubleshooting_warning_suppression()

    async def test_suppress_and_restore_all_persist_the_list(self) -> None:
        code = "proactive.loop_stale"
        key = self.api._troubleshooting_semantic_warning_type(code)

        suppressed = await self._post(
            {
                "action": "suppress",
                "key": "warning:00000000000000000000",
                "code": code,
                "title": "主动循环心跳不新鲜",
                "source": "常见检查",
            }
        )
        self.assertTrue(suppressed["success"])
        self.assertEqual(len(self.api.plugin.data["troubleshooting_suppressed_warning_types"]), 1)
        self.assertEqual(self.api.plugin.data["troubleshooting_suppressed_warning_types"][0]["key"], key)
        self.assertEqual(self.api.plugin.data["troubleshooting_suppressed_warning_types"][0]["code"], code)

        restored = await self._post({"action": "restore_all"})
        self.assertTrue(restored["success"])
        self.assertEqual(self.api.plugin.data["troubleshooting_suppressed_warning_types"], [])
        self.assertEqual(self.api.plugin.save_count, 2)


if __name__ == "__main__":
    unittest.main()
