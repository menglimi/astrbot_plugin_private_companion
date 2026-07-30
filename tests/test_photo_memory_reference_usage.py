# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from types import SimpleNamespace

from astrbot_plugin_private_companion.memory_companion_adapter import MemoryCompanionAdapterMixin


class _Event:
    unified_msg_origin = "default:FriendMessage:10001"

    def is_private_chat(self) -> bool:
        return True

    def get_sender_id(self) -> str:
        return "10001"


class _Harness(MemoryCompanionAdapterMixin):
    def __init__(self) -> None:
        self.records: list[dict] = []

    def _memory_companion_bridge(self):
        async def record_event(**kwargs):
            self.records.append(dict(kwargs))

        return SimpleNamespace(record_event=record_event)

    @staticmethod
    def _sender_display_name(_event) -> str:
        return "测试用户"

    @staticmethod
    def _memory_companion_now_iso() -> str:
        return "2026-07-23T12:00:00+08:00"


class PhotoMemoryReferenceUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_true_is_recorded_without_text_marker(self) -> None:
        harness = _Harness()

        await harness._memory_companion_record_photo_generation(
            _Event(),
            prompt="自然自拍",
            image_path="C:/generated/result.png",
            note="ok (generate_selfie)",
            reference_image_path="C:/reference/persona.png",
            reference_used=True,
        )

        record = harness.records[0]
        self.assertIn("使用了参考图", record["content"])
        self.assertTrue(record["metadata"]["used_reference"])

    async def test_structured_false_overrides_unrelated_used_wording(self) -> None:
        harness = _Harness()

        await harness._memory_companion_record_photo_generation(
            _Event(),
            prompt="自然自拍",
            image_path="C:/generated/result.png",
            note="ok；已使用在线 API #1",
            reference_image_path="C:/reference/persona.png",
            reference_used=False,
        )

        record = harness.records[0]
        self.assertNotIn("使用了参考图", record["content"])
        self.assertFalse(record["metadata"]["used_reference"])

    async def test_legacy_note_still_confirms_reference_when_structured_value_is_unknown(self) -> None:
        harness = _Harness()

        await harness._memory_companion_record_photo_generation(
            _Event(),
            prompt="自然自拍",
            image_path="C:/generated/result.png",
            note="ok；已使用本地人设参考图",
            reference_image_path="C:/reference/persona.png",
        )

        self.assertTrue(harness.records[0]["metadata"]["used_reference"])


if __name__ == "__main__":
    unittest.main()
