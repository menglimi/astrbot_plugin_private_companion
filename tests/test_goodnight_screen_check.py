# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import json
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin


class _ScreenPlugin:
    def __init__(self, result: str = '{"state":"inactive","reason":"没有明确活动"}', callback=None) -> None:
        self.result = result
        self.callback = callback
        self.calls = 0
        self.prompts: list[str] = []

    @staticmethod
    def _create_virtual_event(target: str):
        return {"target": target}

    async def _invoke_screen_skill(self, _event, *, request_prompt: str, history_user_text: str, task_id: str):
        self.calls += 1
        self.prompts.append(request_prompt)
        if callable(self.callback):
            self.callback()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class _GoodnightScreenHarness(ProactiveMessageMixin):
    enable_screen_glance_action = True
    enable_goodnight_screen_check = True
    goodnight_screen_check_delay_minutes = 45
    enable_llm_proactive_message = True

    def __init__(self, *, screen_result: str = '{"state":"inactive","reason":"没有明确活动"}') -> None:
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.generated: list[dict] = []
        self.sent: list[tuple[str, str]] = []
        self.screen_attempts = 0
        self.screen_plugin = _ScreenPlugin(screen_result)
        self.data = {
            "users": {
                "10001": {
                    "user_id": "10001",
                    "nickname": "主要用户",
                    "enabled": True,
                    "umo": "default:FriendMessage:10001",
                    "relationship_role": "owner",
                    "user_rest_kind": "sleep",
                    "user_rest_set_at": 100.0,
                    "user_rest_until": 10000.0,
                    "user_rest_reason": "晚安，我先睡了",
                    "last_user_message_at": 100.0,
                    "last_activity_at": 100.0,
                    "sent_day": "test-day",
                    "sent_today": 0,
                    "relationship_state": {},
                }
            }
        }

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"][str(user_id)]

    @staticmethod
    def _private_user_role(user: dict, user_id: str = "") -> str:
        return str(user.get("relationship_role") or "friend")

    @staticmethod
    def _user_enabled_for_proactive(_user_id: str, user: dict) -> bool:
        return bool(user.get("enabled", True))

    @staticmethod
    def _proactive_generation_disabled(_user=None) -> bool:
        return False

    @staticmethod
    def _effective_user_daily_limit(_user: dict) -> int:
        return 8

    @staticmethod
    def _proactive_daily_limit_is_unlimited(_limit: int) -> bool:
        return False

    @staticmethod
    def _user_rest_signal_should_block_current_reply(text: str) -> bool:
        return bool(re.search(r"别.{0,8}(?:打扰|主动|找我|发消息)", str(text or "")))

    @staticmethod
    def _user_rest_silence_until(user: dict, *, now: float | None = None) -> float:
        if max(float(user.get("last_activity_at") or 0), float(user.get("last_user_message_at") or 0)) > float(
            user.get("user_rest_set_at") or 0
        ):
            return 0.0
        return float(user.get("user_rest_until") or 0)

    @staticmethod
    def _reset_daily_counter_if_needed(user: dict) -> None:
        user.setdefault("sent_today", 0)

    def _screen_glance_available(self, _user: dict) -> bool:
        return self.screen_plugin is not None

    def _get_screen_companion_plugin(self):
        return self.screen_plugin

    def _note_screen_peek_attempt(self, user_id: str, reason: str = "", *, count_daily: bool = True) -> None:
        self.screen_attempts += 1
        user = self._get_user(user_id)
        user["last_screen_peek_reason"] = reason
        if count_daily:
            user["screen_peek_today"] = int(user.get("screen_peek_today") or 0) + 1

    @staticmethod
    def _note_screen_peek_failure(_user: dict, _reason: str = "") -> None:
        return None

    @staticmethod
    def _parse_json_object(raw):
        if isinstance(raw, dict):
            return raw
        return json.loads(str(raw))

    def _save_data_sync(self) -> None:
        self.saved += 1

    async def _generate_proactive_message_with_llm(
        self,
        user: dict,
        name: str,
        reason: str,
        action_context: str = "",
        action: str = "message",
        motive: str = "",
    ) -> str:
        self.generated.append(
            {
                "user": user,
                "name": name,
                "reason": reason,
                "action_context": action_context,
                "action": action,
                "motive": motive,
            }
        )
        return "还没睡的话，忙完就早点休息，不用回我。"

    @staticmethod
    async def _review_proactive_message_send_decision(_user, _text, **_kwargs):
        return {"decision": "send", "text": ""}

    async def _send_proactive_message_chain(self, umo: str, text: str, *_args, **_kwargs):
        self.sent.append((umo, text))
        return SimpleNamespace(delivered=True, complete=True)

    @staticmethod
    def _visible_text_without_tts_reading(text: str, *, limit: int = 500) -> str:
        return str(text or "")[:limit]


class GoodnightScreenScheduleTests(unittest.TestCase):
    def test_mutual_goodnight_schedules_once(self) -> None:
        harness = _GoodnightScreenHarness()
        user = harness._get_user("10001")

        first = harness._maybe_schedule_goodnight_screen_check(user, "晚安，做个好梦。", now=110.0)
        second = harness._maybe_schedule_goodnight_screen_check(user, "晚安。", now=111.0)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(2810.0, user["goodnight_screen_check_due_at"])

    def test_nap_explicit_quiet_and_secondary_user_do_not_schedule(self) -> None:
        for update in (
            {"user_rest_kind": "nap"},
            {"user_rest_reason": "晚安，今晚别主动找我"},
            {"relationship_role": "friend"},
        ):
            harness = _GoodnightScreenHarness()
            user = harness._get_user("10001")
            user.update(update)
            self.assertFalse(harness._maybe_schedule_goodnight_screen_check(user, "晚安，好梦。", now=110.0))


class GoodnightScreenProcessTests(unittest.TestCase):
    @staticmethod
    def _make_due(harness: _GoodnightScreenHarness) -> dict:
        user = harness._get_user("10001")
        user.update(
            {
                "goodnight_screen_check_due_at": 150.0,
                "goodnight_screen_check_episode_at": 100.0,
                "goodnight_screen_check_episode_key": "10001:100.000",
            }
        )
        return user

    def test_inactive_is_silent_and_episode_is_consumed(self) -> None:
        harness = _GoodnightScreenHarness(screen_result='{"state":"inactive","reason":"没有明确活动"}')
        user = self._make_due(harness)

        with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
            asyncio.run(harness._maybe_process_goodnight_screen_checks())
            asyncio.run(harness._maybe_process_goodnight_screen_checks())

        self.assertEqual(1, harness.screen_plugin.calls)
        self.assertEqual([], harness.sent)
        self.assertEqual(0, user["goodnight_screen_check_due_at"])
        self.assertEqual("inactive", user["goodnight_screen_check_state"])

    def test_active_sends_once_without_forwarding_screen_content(self) -> None:
        sensitive = "正在看聊天窗口，账号 Alice，文字是 secret"
        harness = _GoodnightScreenHarness(
            screen_result=json.dumps({"state": "active", "reason": sensitive}, ensure_ascii=False)
        )
        user = self._make_due(harness)

        with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
            asyncio.run(harness._maybe_process_goodnight_screen_checks())

        self.assertEqual(1, len(harness.sent))
        self.assertEqual("reminded", user["goodnight_screen_check_state"])
        self.assertEqual(1, user["sent_today"])
        self.assertEqual("goodnight_screen_check", harness.generated[0]["reason"])
        generation_input = " ".join(str(value) for value in harness.generated[0].values() if not isinstance(value, dict))
        self.assertNotIn("Alice", generation_input)
        self.assertNotIn("secret", generation_input)
        self.assertNotIn("聊天窗口", generation_input)
        self.assertIn("不要转述或摘录任何屏幕内容", harness.screen_plugin.prompts[0])

    def test_user_activity_before_due_cancels_without_screen_call(self) -> None:
        harness = _GoodnightScreenHarness(screen_result='{"state":"active","reason":"仍在操作"}')
        user = self._make_due(harness)
        user["last_user_message_at"] = 180.0

        with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
            asyncio.run(harness._maybe_process_goodnight_screen_checks())

        self.assertEqual(0, harness.screen_plugin.calls)
        self.assertEqual([], harness.sent)
        self.assertEqual("user_active_after_goodnight", user["goodnight_screen_check_state"])

    def test_activity_during_screen_check_cancels_before_send(self) -> None:
        harness = _GoodnightScreenHarness(screen_result='{"state":"active","reason":"仍在操作"}')
        user = self._make_due(harness)
        harness.screen_plugin.callback = lambda: user.update({"last_user_message_at": 190.0})

        with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
            asyncio.run(harness._maybe_process_goodnight_screen_checks())

        self.assertEqual(1, harness.screen_plugin.calls)
        self.assertEqual([], harness.sent)
        self.assertEqual("user_active_after_goodnight", user["goodnight_screen_check_state"])

    def test_screen_failure_is_not_retried(self) -> None:
        harness = _GoodnightScreenHarness()
        harness.screen_plugin.result = RuntimeError("vision unavailable")
        user = self._make_due(harness)

        with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
            asyncio.run(harness._maybe_process_goodnight_screen_checks())
            asyncio.run(harness._maybe_process_goodnight_screen_checks())

        self.assertEqual(1, harness.screen_plugin.calls)
        self.assertEqual(1, harness.screen_attempts)
        self.assertEqual([], harness.sent)
        self.assertEqual("uncertain", user["goodnight_screen_check_state"])

    def test_daily_limit_and_relationship_backoff_block_before_screen(self) -> None:
        cases = (
            {"sent_today": 8},
            {"relationship_state": {"mode": "hurt", "hurt_until": 500.0}},
        )
        expected = ("daily_proactive_limit", "relationship_backoff")
        for update, state in zip(cases, expected):
            harness = _GoodnightScreenHarness(screen_result='{"state":"active","reason":"仍在操作"}')
            user = self._make_due(harness)
            user.update(update)
            with patch("astrbot_plugin_private_companion.proactive_message._now_ts", return_value=200.0):
                asyncio.run(harness._maybe_process_goodnight_screen_checks())
            self.assertEqual(0, harness.screen_plugin.calls)
            self.assertEqual(state, user["goodnight_screen_check_state"])


if __name__ == "__main__":
    unittest.main()
