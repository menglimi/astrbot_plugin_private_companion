# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.daily_state import DailyStateMixin
from astrbot_plugin_private_companion.core_store import CoreStoreMixin
from astrbot_plugin_private_companion.memo_notes import advance_recurring_memo_due, memo_note_due_state, normalize_memo_note
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


NOW = datetime(2026, 7, 14, 10, 0).timestamp()


class MemoHarness(DailyStateMixin):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.saved = 0
        self.offered: list[dict] = []
        self.data = {
            "memo_notes": [],
            "users": {"owner": {"umo": "aiocqhttp:FriendMessage:10001", "enabled": True}},
        }

    def _save_data_sync(self, **_kwargs):
        self.saved += 1

    def _environment_fromtimestamp(self, value):
        return datetime.fromtimestamp(float(value))

    def _format_timestamp_elapsed(self, value):
        return datetime.fromtimestamp(float(value)).strftime("%Y-%m-%d %H:%M") if value else ""

    def _personal_goal_owner_users(self):
        return [("owner", self.data["users"]["owner"])]

    def _offer_proactive_candidate(self, _user_id, user, candidate):
        self.offered.append(candidate)
        user[candidate["context_key"]] = candidate["context"]
        return True


class MemoNoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = MemoHarness()
        self.api = PrivateCompanionPageApi(self.plugin)

    async def call_update(self, payload):
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            return await self.api.update_memo_note()

    async def test_create_and_edit_note_through_page_api(self):
        result = await self.call_update({
            "title": "提交材料",
            "content": "身份证复印件\n申请表",
            "due_at": NOW + 3600,
            "repeat": "none",
            "color": "blue",
            "pinned": True,
            "remind_enabled": True,
        })
        self.assertTrue(result["success"])
        note = result["data"]["memo_notes"]["items"][0]
        self.assertEqual("blue", note["color"])
        self.assertTrue(note["pinned"])
        self.assertEqual("身份证复印件\n申请表", note["content"])
        edited = await self.call_update({
            "id": note["id"],
            "title": "提交更新材料",
            "content": note["content"],
            "due_at": note["due_at"],
            "repeat": "none",
            "color": "green",
            "remind_enabled": True,
        })
        self.assertEqual("提交更新材料", edited["data"]["memo_notes"]["items"][0]["title"])

    async def test_repeat_requires_due_time(self):
        result = await self.call_update({"title": "喝水", "repeat": "daily", "due_at": 0})
        self.assertFalse(result["success"])
        self.assertIn("到期时间", result["error"])

    async def test_list_notes_does_not_require_bookshelf_unlock(self):
        self.plugin.data["memo_notes"] = [{
            "id": "memo-public-workspace",
            "title": "独立便签",
            "status": "active",
            "created_at": NOW,
        }]
        result = await self.api.list_memo_notes()
        self.assertTrue(result["success"])
        self.assertEqual("独立便签", result["data"]["memo_notes"]["items"][0]["title"])

    async def test_completing_recurring_note_moves_to_next_due(self):
        self.plugin.data["memo_notes"] = [{
            "id": "memo-repeat",
            "title": "每周整理",
            "status": "active",
            "due_at": NOW - 60,
            "repeat": "weekly",
            "remind_enabled": True,
            "created_at": NOW - 86400,
        }]
        with patch("astrbot_plugin_private_companion.page_api.time.time", return_value=NOW):
            result = await self.call_update({"action": "complete", "id": "memo-repeat"})
        note = result["data"]["memo_notes"]["items"][0]
        self.assertEqual("active", note["status"])
        self.assertGreater(note["due_at"], NOW)
        self.assertEqual(1, note["completion_count"])

    async def test_due_note_offers_once_per_day(self):
        self.plugin.data["memo_notes"] = [{
            "id": "memo-due",
            "title": "交材料",
            "content": "发给老师",
            "status": "active",
            "due_at": NOW - 60,
            "repeat": "none",
            "remind_enabled": True,
            "created_at": NOW - 86400,
        }]
        with patch("astrbot_plugin_private_companion.daily_state._now_ts", return_value=NOW):
            await self.plugin._maybe_process_memo_notes()
            await self.plugin._maybe_process_memo_notes()
        self.assertEqual(1, len(self.plugin.offered))
        self.assertEqual("memo_note_reminder", self.plugin.offered[0]["reason"])
        prompt = self.plugin._format_memo_note_prompt(self.plugin.data["users"]["owner"], reason="memo_note_reminder")
        self.assertIn("交材料", prompt)
        self.assertIn("不解释便签系统", prompt)
        self.assertGreaterEqual(self.plugin._next_memo_due_in_seconds(NOW), 23 * 3600)

    def test_normalization_and_due_state(self):
        note = normalize_memo_note({"id": "one", "content": "  第一行  \n 第二行 ", "due_at": NOW - 100})
        self.assertEqual("第一行\n第二行", note["content"])
        self.assertEqual("overdue", memo_note_due_state(note, now=NOW))

    def test_monthly_repeat_handles_short_month(self):
        due = datetime(2026, 1, 31, 9, 0).timestamp()
        next_due = advance_recurring_memo_due(due, "monthly", now=due, anchor_day=31)
        self.assertEqual((2026, 2, 28), datetime.fromtimestamp(next_due).date().timetuple()[:3])
        march_due = advance_recurring_memo_due(next_due, "monthly", now=next_due, anchor_day=31)
        self.assertEqual((2026, 3, 31), datetime.fromtimestamp(march_due).date().timetuple()[:3])

    def test_bookshelf_ui_contains_complete_memo_workflow(self):
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        html = (root / "pages" / "陪伴面板" / "index.html").read_text(encoding="utf-8")
        script = (root / "pages" / "陪伴面板" / "app.js").read_text(encoding="utf-8")
        css = (root / "pages" / "陪伴面板" / "app.css").read_text(encoding="utf-8")
        for value in ("memoEditorForm", "memoNoteGrid", "memoDueAt", "memoRepeat", "memoRemind"):
            self.assertIn(value, html)
        for value in ("/memo/list", "/memo/update", "data-memo-action", "renderMemoNotes", "updateMemoAction"):
            self.assertIn(value, script)
        self.assertIn("state.memoNotes", script)
        self.assertIn('tabName === "creative"', script)
        self.assertIn(".memo-note-grid", css)
        self.assertIn(".memo-note.is-overdue", css)

    def test_memo_body_is_preserved_as_raw_user_text(self):
        self.assertTrue(CoreStoreMixin._store_path_is_raw_user_text(("memo_notes", 0, "content")))
        self.assertTrue(CoreStoreMixin._store_path_is_raw_user_text(("memo_notes", 0, "title")))


if __name__ == "__main__":
    unittest.main()
