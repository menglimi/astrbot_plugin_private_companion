# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin


class _Event:
    def __init__(self, message: str, *, group_id: str = "300000001") -> None:
        self.message_str = message
        self.group_id = group_id
        self.stopped = False

    @staticmethod
    def plain_result(text: str) -> str:
        return text

    @staticmethod
    def chain_result(chain):
        return chain

    @staticmethod
    def get_sender_id() -> str:
        return "operator-1"

    def stop_event(self) -> None:
        self.stopped = True


class _Harness(CommandHandlersMixin):
    enable_group_companion = True
    group_access_mode = "whitelist"

    def __init__(self, *, authorized: bool = True) -> None:
        self.authorized = authorized
        self._data_lock = asyncio.Lock()
        self.block_item: dict = {}
        self.saved_sections: list[set[str]] = []

    @staticmethod
    def _extract_group_id_from_event(event: _Event) -> str:
        return event.group_id

    @staticmethod
    def _group_allowed_by_access_mode(_group_id: str) -> bool:
        return True

    @staticmethod
    def _configured_group_blacklist_ids() -> list[str]:
        return []

    def _can_manage_group_companion(self, _event: _Event) -> bool:
        return self.authorized

    @staticmethod
    def _management_denied_text() -> str:
        return "DENIED"

    def _set_group_llm_reply_block(self, _group_id: str, enabled: bool, **_kwargs):
        self.block_item = {"enabled": enabled, "updated_at": 100.0} if enabled else {}
        return self.block_item

    def _group_llm_reply_block_item(self, _group_id: str) -> dict:
        return self.block_item

    def _save_data_sync(self, *, sections: set[str]) -> None:
        self.saved_sections.append(sections)

    @staticmethod
    def _format_timestamp_elapsed(_timestamp: float) -> str:
        return "刚刚"


class GroupCommandBehaviorContractTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _run(harness: _Harness, message: str, *, group_id: str = "300000001"):
        event = _Event(message, group_id=group_id)
        responses = [item async for item in harness._group_companion_command_impl(event)]
        return event, responses

    async def test_requires_group_context_with_compatible_text(self) -> None:
        event, responses = await self._run(_Harness(), "陪伴群 状态", group_id="")
        self.assertEqual(["这条命令需要在群聊里使用。"], responses)
        self.assertFalse(event.stopped)

    async def test_llm_close_alias_accepts_split_value_and_preserves_response(self) -> None:
        harness = _Harness()
        event, responses = await self._run(harness, "陪伴群 关闭 LLM回复")
        self.assertEqual(
            ["已关闭本群所有 LLM 回复。\n群号：300000001\n状态：拦截中（刚刚）\n恢复：陪伴群 开启LLM"],
            responses,
        )
        self.assertEqual([{"group_llm_reply_blocks"}], harness.saved_sections)
        self.assertTrue(event.stopped)

    async def test_llm_management_permission_denial_is_compatible(self) -> None:
        event, responses = await self._run(_Harness(authorized=False), "陪伴群 禁用LLM")
        self.assertEqual(["DENIED"], responses)
        self.assertFalse(event.stopped)

    async def test_llm_status_and_restore_texts_are_compatible(self) -> None:
        harness = _Harness()
        await self._run(harness, "陪伴群 关闭LLM")

        status_event, status = await self._run(harness, "陪伴群 LLM 状态")
        self.assertEqual(["本群 LLM 回复当前关闭中，开启时间：刚刚。\n恢复：陪伴群 开启LLM"], status)
        self.assertTrue(status_event.stopped)

        restore_event, restored = await self._run(harness, "陪伴群 恢复主链回复")
        self.assertEqual(["已恢复本群 LLM 回复。"], restored)
        self.assertTrue(restore_event.stopped)


if __name__ == "__main__":
    unittest.main()
