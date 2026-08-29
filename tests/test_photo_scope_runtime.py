from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime
from typing import Any

from astrbot_plugin_private_companion.command_handlers import CommandHandlersMixin

from .test_photo_tool_delivery_contract import (
    _CommandEntryPhotoHarness,
    _FakeEvent,
    _PhotoToolHarness,
)


TODAY = datetime(2026, 8, 10, 12, 0, 0)


class _FakeGroupEvent(_FakeEvent):
    unified_msg_origin = "default:GroupMessage:group-1"

    @staticmethod
    def is_private_chat() -> bool:
        return False


class _ScopedCommandHarness(_CommandEntryPhotoHarness):
    def __init__(self) -> None:
        super().__init__()
        self.scope_left: int | None = 1
        self.scope_attempts: list[str] = []
        self.command_attempts = 0
        self.rule_attempts = 0

    @staticmethod
    def _photo_generation_scope(*_args: Any, **_kwargs: Any) -> str:
        return "private_owner"

    def _photo_generation_scope_quota_left(self, *_args: Any, **_kwargs: Any) -> int | None:
        return self.scope_left

    def _photo_generation_scope_allowed(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.scope_left != 0

    @staticmethod
    def _photo_generation_scope_quota_block_message(*_args: Any, **_kwargs: Any) -> str:
        return "scope quota exhausted"

    def _note_photo_generation_scope_attempt(self, *_args: Any, scope: str = "", **_kwargs: Any) -> None:
        self.scope_attempts.append(scope)

    def _note_command_photo_generation_attempt(self, _user: dict[str, Any], image_path: str = "") -> None:
        self.command_attempts += 1

    def _note_natural_language_photo_generation_attempt(self, _user: dict[str, Any], image_path: str = "") -> None:
        self.rule_attempts += 1


class _ScopedToolHarness(_PhotoToolHarness, CommandHandlersMixin):
    def __init__(self) -> None:
        super().__init__()
        self._data_lock = asyncio.Lock()
        self.data: dict[str, Any] = {"users": {}}
        self.command_photo_generation_max_daily = -1
        self.scope_left: int | None = 1
        self.scope_attempts: list[str] = []
        self.proactive_attempts = 0
        self.proactive_available = True
        self.group_enabled = False
        self.target_user = True
        self.failure_note = "ok"
        self.save_calls = 0
        self.tool_timeout = 120.0

    def _environment_now(self) -> datetime:
        return TODAY

    def _get_user(self, user_id: str) -> dict[str, Any]:
        users = self.data.setdefault("users", {})
        return users.setdefault(
            user_id,
            {
                "user_id": user_id,
                "enabled": True,
                "relationship_role": "owner",
                "command_photo_generated_day": TODAY.strftime("%Y-%m-%d"),
                "command_photo_generated_today": 0,
            },
        )

    def _is_target_private_user(self, _user_id: str, _user: dict[str, Any] | None) -> bool:
        return self.target_user

    @staticmethod
    def _private_user_id_for_event(_event: Any, user_id: str) -> str:
        return user_id

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return user_id

    @staticmethod
    def _extract_group_id_from_event(event: Any) -> str:
        return "group-1" if ":GroupMessage:" in str(getattr(event, "unified_msg_origin", "")) else ""

    def _group_enabled_for_event(self, _group_id: str) -> bool:
        return self.group_enabled

    @staticmethod
    def _photo_generation_scope(event: Any, **_kwargs: Any) -> str:
        if bool(getattr(event, "private_companion_proactive_framework", False)):
            return "proactive"
        if ":GroupMessage:" in str(getattr(event, "unified_msg_origin", "")):
            return "group"
        return "private_owner"

    def _photo_generation_scope_quota_left(self, *_args: Any, **_kwargs: Any) -> int | None:
        return self.scope_left

    def _photo_generation_scope_allowed(self, *_args: Any, **_kwargs: Any) -> bool:
        return self.scope_left != 0

    @staticmethod
    def _photo_generation_scope_quota_block_message(*_args: Any, **_kwargs: Any) -> str:
        return "scope quota exhausted"

    def _note_photo_generation_scope_attempt(self, *_args: Any, scope: str = "", **_kwargs: Any) -> None:
        self.scope_attempts.append(scope)

    def _photo_text_available(self, user: dict[str, Any] | None = None) -> bool:
        return self.proactive_available if isinstance(user, dict) else True

    def _note_photo_generation_attempt(self, _user_id: str, image_path: str = "") -> None:
        self.proactive_attempts += 1

    @staticmethod
    def _photo_generation_failure_counts_as_attempt(note: str) -> bool:
        return "HTTP" in str(note or "")

    def _save_data_sync(self, **_kwargs) -> None:
        self.save_calls += 1

    def _photo_tool_call_timeout_seconds(self) -> float:
        return self.tool_timeout

    async def _generate_photo_image(self, **kwargs: Any):
        self.generation_kwargs = dict(kwargs)
        self.workflow_kind = str(kwargs.get("workflow_kind") or "")
        if self.generation_delay:
            await asyncio.sleep(self.generation_delay)
        return "test-backend", self.image_path, self.failure_note


class PhotoScopeCommandRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"test-image")
        handle.close()
        self.image_path = handle.name

    def tearDown(self) -> None:
        if os.path.exists(self.image_path):
            os.unlink(self.image_path)

    async def test_explicit_command_checks_scope_quota_after_user_authorization(self) -> None:
        harness = _ScopedCommandHarness()
        harness.scope_left = 0
        harness.image_path = self.image_path
        event = _FakeEvent()

        handled = await harness._handle_companion_photo_command(
            event,
            "10001",
            "generate",
            "summer beach",
        )

        self.assertTrue(handled)
        self.assertTrue(event.stopped)
        self.assertEqual({}, harness.generation_kwargs)
        self.assertIn("scope quota exhausted", harness.replies)

    async def test_explicit_command_records_command_and_scope_attempts_together(self) -> None:
        harness = _ScopedCommandHarness()
        harness.image_path = self.image_path

        await harness._handle_companion_photo_command(
            _FakeEvent(),
            "10001",
            "generate",
            "summer beach",
        )

        self.assertEqual(1, harness.command_attempts)
        self.assertEqual(["private_owner"], harness.scope_attempts)

    async def test_rule_fast_records_rule_and_scope_attempts_together(self) -> None:
        harness = _ScopedCommandHarness()
        harness.image_path = self.image_path

        handled = await harness._maybe_handle_natural_language_photo_request(
            _FakeEvent(),
            "10001",
            "draw a summer beach",
            directed=True,
        )

        self.assertTrue(handled)
        self.assertEqual(1, harness.rule_attempts)
        self.assertEqual(["private_owner"], harness.scope_attempts)


class PhotoScopeToolRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        handle = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        handle.write(b"test-image")
        handle.close()
        self.image_path = handle.name

    def tearDown(self) -> None:
        if os.path.exists(self.image_path):
            os.unlink(self.image_path)

    async def test_group_tool_counts_authorized_requester_even_when_not_private_target(self) -> None:
        harness = _ScopedToolHarness()
        harness.group_enabled = True
        harness.target_user = False
        harness.image_path = self.image_path

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeGroupEvent(),
                prompt="summer beach",
                send=False,
            )
        )

        self.assertEqual("success", payload["status"])
        user = harness.data["users"]["10001"]
        self.assertEqual(1, user["command_photo_generated_today"])
        self.assertEqual(["group"], harness.scope_attempts)

    async def test_countable_tool_failure_records_command_and_scope_attempts(self) -> None:
        harness = _ScopedToolHarness()
        harness.image_path = ""
        harness.failure_note = "HTTP 500 from image provider"

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="summer beach",
                send=False,
            )
        )

        self.assertEqual("submission_unconfirmed", payload["status"])
        self.assertFalse(payload["same_turn_retry_allowed"])
        user = harness.data["users"]["10001"]
        self.assertEqual(1, user["command_photo_generated_today"])
        self.assertEqual(["private_owner"], harness.scope_attempts)

    async def test_proactive_tool_uses_proactive_quota_instead_of_command_quota(self) -> None:
        harness = _ScopedToolHarness()
        harness.command_photo_generation_max_daily = 0
        harness.image_path = self.image_path
        event = _FakeEvent()
        event.private_companion_proactive_framework = True

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                event,
                prompt="window selfie",
                send=False,
            )
        )

        self.assertEqual("success", payload["status"])
        user = harness.data["users"]["10001"]
        self.assertEqual(0, user["command_photo_generated_today"])
        self.assertEqual(1, harness.proactive_attempts)
        self.assertEqual(["proactive"], harness.scope_attempts)

    async def test_tool_timeout_counts_as_command_and_scope_attempt(self) -> None:
        harness = _ScopedToolHarness()
        harness.tool_timeout = 0.05
        harness.generation_delay = 1.0

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="summer beach",
                send=False,
            )
        )

        self.assertEqual("timeout", payload["status"])
        user = harness.data["users"]["10001"]
        self.assertEqual(1, user["command_photo_generated_today"])
        self.assertEqual(["private_owner"], harness.scope_attempts)

    async def test_scope_exhaustion_blocks_tool_before_backend(self) -> None:
        harness = _ScopedToolHarness()
        harness.scope_left = 0

        payload = json.loads(
            await harness._pc_generate_photo_impl(
                _FakeEvent(),
                prompt="summer beach",
            )
        )

        self.assertEqual("quota_exhausted", payload["status"])
        self.assertEqual({}, harness.generation_kwargs)


if __name__ == "__main__":
    unittest.main()
