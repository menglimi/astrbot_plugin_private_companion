# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


OWNER_ID = "10001"
MEMBER_ID = "20002"
OWNER_UMO = f"default:FriendMessage:{OWNER_ID}"
MEMBER_UMO = f"default:FriendMessage:{MEMBER_ID}"
GROUP_UMO = "default:GroupMessage:30003"


class _TtsProvider:
    meta = {"id": "test_tts", "name": "测试 TTS"}


class _TtsContext:
    def __init__(self, provider: Any) -> None:
        self.provider = provider
        self.config_umos: list[str] = []
        self.provider_umos: list[str] = []

    def get_config(self, umo: str = "") -> dict[str, Any]:
        self.config_umos.append(umo)
        return {"provider_tts_settings": {}}

    def get_using_tts_provider(self, umo: str = "") -> Any:
        self.provider_umos.append(umo)
        return self.provider


class _TtsTroubleshootingPlugin:
    def __init__(
        self,
        audio_path: Path,
        users: dict[str, dict[str, Any]],
        roles: dict[str, str],
        *,
        delivery_result: bool = True,
        delivery_error: Exception | None = None,
    ) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {"users": users}
        self.context = _TtsContext(_TtsProvider())
        self.audio_path = audio_path
        self.component = object()
        self.roles = roles
        self.delivery_result = delivery_result
        self.delivery_error = delivery_error
        self.record_calls: list[tuple[Any, ...]] = []
        self.delivery_calls: list[tuple[str, list[Any], bool]] = []

    def _private_user_role(self, _user: dict[str, Any], user_id: str = "") -> str:
        return self.roles.get(str(user_id), "member")

    @staticmethod
    def _resolve_tts_synthesis_provider(_event: Any, provider: Any) -> Any:
        return provider

    async def _tts_record_component(self, *args: Any, **kwargs: Any) -> Any:
        self.record_calls.append((*args, kwargs))
        return self.component

    def _tts_record_refs(self, component: Any) -> list[str]:
        return [str(self.audio_path)] if component is self.component else []

    def _private_delivery_umo_for_user_id(self, user_id: str) -> str:
        user = self.data["users"].get(str(user_id), {})
        return str(user.get("umo") or "")

    async def _send_chain_components(
        self,
        umo: str,
        components: list[Any],
        *,
        apply_decorating_hooks: bool = True,
    ) -> bool:
        self.delivery_calls.append((umo, components, apply_decorating_hooks))
        if self.delivery_error is not None:
            raise self.delivery_error
        return self.delivery_result


def _user(user_id: str, umo: str) -> dict[str, Any]:
    return {
        "user_id": user_id,
        "nickname": f"用户 {user_id}",
        "enabled": True,
        "umo": umo,
    }


class TtsTroubleshootingDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_temp_dir)
        self.audio_path = Path(self.temp_dir.name) / "tts-test.wav"
        self.audio_path.write_bytes(b"RIFF-test-audio")

    async def _cleanup_temp_dir(self) -> None:
        self.temp_dir.cleanup()

    def _plugin(
        self,
        users: dict[str, dict[str, Any]],
        roles: dict[str, str],
        *,
        delivery_result: bool = True,
        delivery_error: Exception | None = None,
    ) -> _TtsTroubleshootingPlugin:
        return _TtsTroubleshootingPlugin(
            self.audio_path,
            users,
            roles,
            delivery_result=delivery_result,
            delivery_error=delivery_error,
        )

    async def test_generated_voice_ignores_requested_target_and_delivers_to_owner_private_chat(self) -> None:
        plugin = self._plugin(
            {OWNER_ID: _user(OWNER_ID, OWNER_UMO)},
            {OWNER_ID: "owner"},
        )
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_tts_generation_chain_test({"umo": MEMBER_UMO})

        self.assertTrue(result["generated"])
        self.assertTrue(result["delivered"])
        self.assertTrue(result["ok"])
        self.assertEqual("", result["delivery_error"])
        self.assertEqual(OWNER_UMO, result["delivery_umo"])
        self.assertEqual([OWNER_UMO], plugin.context.config_umos)
        self.assertEqual([OWNER_UMO], plugin.context.provider_umos)
        self.assertEqual(
            [(OWNER_UMO, [plugin.component], False)],
            plugin.delivery_calls,
        )

    async def test_false_delivery_result_is_reported_as_failure(self) -> None:
        plugin = self._plugin(
            {OWNER_ID: _user(OWNER_ID, OWNER_UMO)},
            {OWNER_ID: "owner"},
            delivery_result=False,
        )
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_tts_generation_chain_test({})

        self.assertTrue(result["generated"])
        self.assertFalse(result["delivered"])
        self.assertFalse(result["ok"])
        self.assertTrue(result["delivery_error"])
        self.assertEqual(OWNER_UMO, result["delivery_umo"])
        self.assertEqual(1, len(plugin.delivery_calls))

    async def test_delivery_exception_is_captured_as_failure(self) -> None:
        plugin = self._plugin(
            {OWNER_ID: _user(OWNER_ID, OWNER_UMO)},
            {OWNER_ID: "owner"},
            delivery_error=RuntimeError("测试发送器拒绝投递"),
        )
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_tts_generation_chain_test({})

        self.assertTrue(result["generated"])
        self.assertFalse(result["delivered"])
        self.assertFalse(result["ok"])
        self.assertIn("测试发送器拒绝投递", result["delivery_error"])
        self.assertEqual(OWNER_UMO, result["delivery_umo"])

    async def test_missing_owner_never_falls_back_to_another_private_user(self) -> None:
        plugin = self._plugin(
            {MEMBER_ID: _user(MEMBER_ID, MEMBER_UMO)},
            {MEMBER_ID: "member"},
        )
        plugin._send_chain_components = AsyncMock(return_value=True)
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_tts_generation_chain_test({})

        self.assertFalse(result["ok"])
        self.assertFalse(result["delivered"])
        self.assertEqual("", result["delivery_umo"])
        plugin._send_chain_components.assert_not_awaited()

    async def test_owner_group_umo_is_rejected_before_delivery(self) -> None:
        plugin = self._plugin(
            {OWNER_ID: _user(OWNER_ID, GROUP_UMO)},
            {OWNER_ID: "owner"},
        )
        plugin._send_chain_components = AsyncMock(return_value=True)
        api = PrivateCompanionPageApi(plugin)

        result = await api._run_tts_generation_chain_test({})

        self.assertFalse(result["ok"])
        self.assertFalse(result["delivered"])
        self.assertNotEqual(GROUP_UMO, result["delivery_umo"])
        self.assertIn("私聊", result["delivery_error"] or result["error"])
        plugin._send_chain_components.assert_not_awaited()

    def test_sanitizer_preserves_generation_and_delivery_diagnostics(self) -> None:
        api = PrivateCompanionPageApi.__new__(PrivateCompanionPageApi)

        result = api._sanitize_troubleshooting_test_result(
            {
                "type": "tts_generation",
                "ok": False,
                "generated": True,
                "delivered": False,
                "delivery_error": "核心发送返回 False",
                "delivery_umo": OWNER_UMO,
            }
        )

        self.assertTrue(result["generated"])
        self.assertFalse(result["delivered"])
        self.assertEqual("核心发送返回 False", result["delivery_error"])
        self.assertEqual(OWNER_UMO, result["delivery_umo"])


if __name__ == "__main__":
    unittest.main()
