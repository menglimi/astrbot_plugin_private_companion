# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.main import PrivateCompanionPlugin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin


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
        self.data = {"users": {"owner": {}, "member": {}}}
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
    def _note_private_user_umo(user_id: str, user: dict, umo: str) -> None:
        return None

    def _save_data_sync(self) -> None:
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
