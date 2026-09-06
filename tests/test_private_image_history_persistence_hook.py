# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.final_response_persistence import FinalResponsePersistenceMixin


class _PersistenceHarness(FinalResponsePersistenceMixin):
    def __init__(self) -> None:
        self.image_history_calls = 0

    @staticmethod
    def _event_message_id(_event) -> str:
        return "image-history-message"

    async def _record_confirmed_outbound_state(self, *_args, **_kwargs):
        return False, set()

    def _stage_delivered_assistant_for_official_history(self, **_kwargs) -> bool:
        return True

    async def _record_final_assistant_in_livingmemory(self, **_kwargs) -> bool:
        return False

    async def _persist_private_image_vision_summary_to_history(self, _event) -> bool:
        self.image_history_calls += 1
        return True


class PrivateImageHistoryPersistenceHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_delivery_persistence_invokes_image_history_writer(self) -> None:
        harness = _PersistenceHarness()
        event = SimpleNamespace(
            _private_companion_persistence_managed=True,
            _has_send_oper=True,
            unified_msg_origin="default:FriendMessage:10001",
        )

        persisted = await harness._finalize_passive_delivered_response(
            event,
            fallback_text="已回复图片",
            force=True,
        )

        self.assertTrue(persisted)
        self.assertEqual(1, harness.image_history_calls)


if __name__ == "__main__":
    unittest.main()
