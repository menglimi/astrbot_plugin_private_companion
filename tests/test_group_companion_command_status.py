# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin


class _FakeEvent:
    message_str = "/陪伴群 状态"

    @staticmethod
    def plain_result(text: str) -> str:
        return text


class _GroupCommandHarness(CommandHandlersMixin):
    group_access_mode = "whitelist"

    def __init__(self, *, global_enabled: bool, allowed: bool) -> None:
        self.enable_group_companion = global_enabled
        self.allowed = allowed

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "300000001"

    def _group_allowed_by_access_mode(self, _group_id: str) -> bool:
        return self.allowed

    @staticmethod
    def _configured_group_blacklist_ids() -> list[str]:
        return []


class GroupCompanionCommandStatusTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _responses(harness: _GroupCommandHarness) -> list[str]:
        return [item async for item in harness._group_companion_command_impl(_FakeEvent())]

    async def test_global_switch_off_is_not_reported_as_missing_whitelist(self) -> None:
        responses = await self._responses(_GroupCommandHarness(global_enabled=False, allowed=True))

        self.assertEqual(1, len(responses))
        self.assertIn("总开关当前关闭", responses[0])
        self.assertNotIn("没有加入群聊陪伴白名单", responses[0])

    async def test_whitelist_rejection_is_still_reported_when_global_switch_is_on(self) -> None:
        responses = await self._responses(_GroupCommandHarness(global_enabled=True, allowed=False))

        self.assertEqual(["这个群还没有加入群聊陪伴白名单，暂时不启用。"], responses)


if __name__ == "__main__":
    unittest.main()
