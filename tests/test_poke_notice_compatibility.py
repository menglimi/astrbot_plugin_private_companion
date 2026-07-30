from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.main import PrivateCompanionPlugin


class _PokeEvent:
    def __init__(self, *, group_id: str = "") -> None:
        raw_message = {
            "post_type": "notice",
            "notice_type": "notify",
            "sub_type": "poke",
            "user_id": "10001",
            "target_id": "20002",
            "self_id": "20002",
        }
        if group_id:
            raw_message["group_id"] = group_id
        self.message_obj = SimpleNamespace(raw_message=raw_message)
        self.message_str = ""
        self.stopped = False

    def stop_event(self) -> None:
        self.stopped = True


class PokeNoticeCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = PrivateCompanionPlugin.__new__(PrivateCompanionPlugin)

    def test_only_onebot_poke_notice_is_recognized(self) -> None:
        event = _PokeEvent()
        self.assertTrue(self.plugin._is_onebot_poke_notice_event(event))

        event.message_obj.raw_message["notice_type"] = "friend_recall"
        self.assertFalse(self.plugin._is_onebot_poke_notice_event(event))

        event.message_obj.raw_message = {"post_type": "message", "message_type": "private"}
        self.assertFalse(self.plugin._is_onebot_poke_notice_event(event))

    async def test_private_poke_bypasses_empty_message_guard(self) -> None:
        event = _PokeEvent()

        await self.plugin.on_private_message(event)

        self.assertFalse(event.stopped)

    async def test_group_poke_bypasses_companion_group_handlers(self) -> None:
        event = _PokeEvent(group_id="30003")

        await self.plugin.capture_group_observation_early(event)
        await self.plugin.on_group_message(event)

        self.assertFalse(event.stopped)

    async def test_poke_bypasses_all_message_side_effects(self) -> None:
        event = _PokeEvent()

        await self.plugin.observe_recall_enhancement_events(event)

        self.assertFalse(event.stopped)


if __name__ == "__main__":
    unittest.main()
