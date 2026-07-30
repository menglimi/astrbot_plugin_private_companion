# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.main import PrivateCompanionExtensionAPI


class _Plugin:
    bot_name = "小星"
    target_platform = "aiocqhttp"

    def __init__(self, self_ids):
        self._self_ids = set(self_ids)
        self._external_realtime_activities = {}
        self.data = {"users": {"10001": {"nickname": "流星"}}}

    def _known_bot_self_ids(self):
        return set(self._self_ids)

    def _build_companion_scene_snapshot(self, user):
        return {"relationship": {"name": (user or {}).get("nickname", "")}}

    @staticmethod
    def _format_companion_scene_snapshot(snapshot, *, purpose="prompt"):
        return f"场景：{snapshot['relationship']['name']}；用途：{purpose}"


class RealtimeExtensionAPITests(unittest.TestCase):
    def test_unique_qq_identity_can_supply_avatar(self) -> None:
        api = PrivateCompanionExtensionAPI(_Plugin({"12345678"}))

        identity = api.get_bot_identity()

        self.assertEqual("12345678", identity["selected_id"])
        self.assertEqual("12345678", identity["qq_id"])
        self.assertIn("nk=12345678", identity["avatar"]["remote_url"])

    def test_multiple_bot_accounts_are_not_guessed(self) -> None:
        api = PrivateCompanionExtensionAPI(_Plugin({"12345678", "22345678"}))

        identity = api.get_bot_identity()

        self.assertEqual("", identity["selected_id"])
        self.assertEqual("", identity["qq_id"])
        self.assertTrue(identity["ambiguous"])

    def test_realtime_context_includes_active_shared_activity(self) -> None:
        api = PrivateCompanionExtensionAPI(_Plugin({"12345678"}))
        api.notify_external_activity_started(
            "together:room",
            user_id="10001",
            kind="shared_watch",
            label="正在一起看《测试影片》",
            source_plugin="astrbot_plugin_together_companion",
        )

        context = api.get_realtime_context("10001", purpose="together")

        self.assertIn("正在一起看《测试影片》", context["prompt"])
        self.assertEqual("shared_watch", context["external_activity"]["kind"])
        self.assertTrue(api.notify_external_activity_ended("together:room"))
        self.assertEqual({}, api.get_external_activity(user_id="10001"))


class RealtimeVoiceExtensionAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_voice_api_forwards_to_plugin_helper(self) -> None:
        plugin = _Plugin({"12345678"})
        plugin._realtime_voice_config = lambda: {
            "available": True,
            "voice_language": "ja",
            "browser_language": "ja-JP",
        }
        plugin._synthesize_realtime_voice = AsyncMock(
            return_value={"available": True, "audio_path": "voice.wav"}
        )
        api = PrivateCompanionExtensionAPI(plugin)
        provider = object()

        self.assertEqual("ja-JP", api.get_realtime_voice_config()["browser_language"])
        result = await api.synthesize_realtime_voice(
            "你好",
            tts_provider=provider,
            source="together_companion",
            play_local=False,
        )

        self.assertEqual("voice.wav", result["audio_path"])
        plugin._synthesize_realtime_voice.assert_awaited_once_with(
            "你好",
            tts_provider=provider,
            provider_settings=None,
            source="together_companion",
            play_local=False,
        )


class _IdentitySourceHarness(CoreStoreMixin):
    pass


class BotIdentitySourceTests(unittest.TestCase):
    def test_onebot_connection_id_is_used_instead_of_internal_client_uuid(self) -> None:
        harness = _IdentitySourceHarness()
        harness.context = SimpleNamespace(
            platform_manager=SimpleNamespace(
                platform_insts=[
                    SimpleNamespace(
                        client_self_id="test-internal-client-id",
                        bot=SimpleNamespace(_wsr_api_clients={"900000001": object()}),
                    )
                ]
            )
        )

        self.assertEqual({"900000001"}, harness._known_bot_self_ids())


if __name__ == "__main__":
    unittest.main()
