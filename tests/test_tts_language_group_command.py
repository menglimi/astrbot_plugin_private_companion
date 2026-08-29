# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin
from astrbot_plugin_private_companion.unified_profile_service import private_companion_gate


class _Event:
    def __init__(self, text: str, sender_id: str, *, private: bool) -> None:
        self.message_str = text
        self.sender_id = sender_id
        self.private = private
        message_type = "FriendMessage" if private else "GroupMessage"
        self.unified_msg_origin = f"default:{message_type}:test"
        self.stopped = False

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_private_chat(self) -> bool:
        return self.private

    def stop_event(self) -> None:
        self.stopped = True


class _Harness(InteractionUtilsMixin, TtsEnhancementMixin):
    def __init__(self) -> None:
        self.require_private_opt_in = True
        self.target_user_ids = ["owner"]
        self.config = {"tts_voice_language": "ja"}
        self.tts_voice_language = "ja"
        self.data = {
            "users": {
                "owner": {
                    "unified_profile_capabilities": {
                        "schema_version": 1,
                        "private_companion_enabled": True,
                        "proactive_private_enabled": False,
                        "portrait_mode": "disabled",
                        "portrait_mode_override": "follow_global",
                        "grant_source": "legacy_configured_target_migration",
                    }
                },
                "member": {},
            }
        }
        self._data_lock = asyncio.Lock()
        self.replies: list[str] = []
        self.save_count = 0

    @staticmethod
    def _qzone_note_event_bot(event) -> None:
        return None

    @staticmethod
    def _normalize_private_identity_id(value) -> str:
        return str(value or "").strip()

    def _configured_target_ids(self) -> list[str]:
        return list(self.target_user_ids)

    @staticmethod
    def _configured_admin_ids() -> set[str]:
        return {"admin"}

    def _get_user(self, user_id: str) -> dict:
        return self.data.setdefault("users", {}).setdefault(user_id, {})

    @staticmethod
    def _sender_display_name(event) -> str:
        return str(event.get_sender_id())

    def _ensure_auto_private_user_profile(self, _event, *, user_id: str, **_kwargs):
        return self._get_user(user_id), False

    @staticmethod
    def _req036_attach_unified_profile_context(_event, **_kwargs) -> dict:
        return {"state": "profile_exact"}

    @staticmethod
    def _schedule_data_save(*_args, **_kwargs) -> None:
        return None

    @staticmethod
    def _req036_private_gate_for_user(user: dict) -> dict:
        return private_companion_gate(user)

    @staticmethod
    def _note_private_user_umo(user_id: str, user: dict, umo: str) -> None:
        return None

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_count += 1

    async def _reply(self, event, text: str, **kwargs) -> None:
        self.replies.append(str(text))

    async def _reply_with_optional_media(self, event, text: str, *args, **kwargs) -> None:
        self.replies.append(str(text))


class TtsLanguageGroupCommandTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, harness: _Harness, event: _Event) -> None:
        await PrivateCompanionPlugin.companion_command(harness, event)

    async def test_owner_can_switch_language_in_group_and_receives_confirmation(self) -> None:
        harness = _Harness()
        event = _Event("陪伴 TTS语种 英语", "owner", private=False)

        await self._run(harness, event)

        self.assertEqual("en", harness.tts_voice_language)
        self.assertEqual("en", harness.data["runtime_settings"]["tts_voice_language"])
        self.assertEqual(1, harness.save_count)
        self.assertEqual(1, len(harness.replies))
        self.assertIn("已切换 TTS 语音语种：英语", harness.replies[0])
        self.assertTrue(event.stopped)

    async def test_plugin_admin_can_restore_config_language_in_group(self) -> None:
        harness = _Harness()
        harness.tts_voice_language = "en"
        harness.data["runtime_settings"] = {"tts_voice_language": "en"}
        event = _Event("陪伴 TTS语种 默认", "admin", private=False)

        await self._run(harness, event)

        self.assertEqual("ja", harness.tts_voice_language)
        self.assertNotIn("tts_voice_language", harness.data["runtime_settings"])
        self.assertEqual(1, len(harness.replies))
        self.assertIn("已恢复 TTS 语音语种为配置页设置：日语", harness.replies[0])

    async def test_regular_group_member_cannot_change_global_language(self) -> None:
        harness = _Harness()
        event = _Event("陪伴 TTS语种 中文", "member", private=False)

        await self._run(harness, event)

        self.assertEqual("ja", harness.tts_voice_language)
        self.assertNotIn("runtime_settings", harness.data)
        self.assertEqual(1, len(harness.replies))
        self.assertIn("需要管理权限", harness.replies[0])
        self.assertTrue(event.stopped)

    async def test_private_owner_command_still_replies(self) -> None:
        harness = _Harness()
        event = _Event("陪伴 TTS语种 中文", "owner", private=True)

        await self._run(harness, event)

        self.assertEqual("zh", harness.tts_voice_language)
        self.assertEqual(1, len(harness.replies))
        self.assertIn("已切换 TTS 语音语种：中文", harness.replies[0])


if __name__ == "__main__":
    unittest.main()
