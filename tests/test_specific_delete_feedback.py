# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class DeleteFeedbackHarness:
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {
            "skill_growth": {"skills": {}},
            "personal_goals": [],
        }
        self.saved = 0

    def _save_data_sync(self, **_kwargs) -> None:
        self.saved += 1

    @staticmethod
    def _format_timestamp_elapsed(_value) -> str:
        return "刚刚"


class SpecificDeleteFeedbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.plugin = DeleteFeedbackHarness()
        self.api = PrivateCompanionPageApi(self.plugin)

    async def _call(self, method, payload: dict) -> dict:
        fake_request = SimpleNamespace(get_json=AsyncMock(return_value=payload))
        with patch("astrbot_plugin_private_companion.page_api.request", fake_request):
            return await method()

    async def test_missing_skill_delete_returns_error_instead_of_false_success(self):
        result = await self._call(
            self.api.update_skill_growth,
            {"id": "missing-skill", "delete": True},
        )

        self.assertFalse(result["success"])
        self.assertIn("没有找到要删除的技能", result["error"])
        self.assertEqual(0, self.plugin.saved)

    async def test_missing_personal_goal_delete_returns_error_instead_of_false_success(self):
        result = await self._call(
            self.api.update_personal_goal,
            {"id": "missing-goal", "delete": True},
        )

        self.assertFalse(result["success"])
        self.assertIn("没有找到要删除的个人目标", result["error"])
        self.assertEqual(0, self.plugin.saved)

    async def test_existing_skill_and_goal_still_delete_successfully(self):
        self.plugin.data["skill_growth"]["skills"]["skill-1"] = {"id": "skill-1", "name": "阅读"}
        self.plugin.data["personal_goals"].append({"id": "goal-1", "title": "读完一本书"})

        skill_result = await self._call(
            self.api.update_skill_growth,
            {"id": "skill-1", "delete": True},
        )
        goal_result = await self._call(
            self.api.update_personal_goal,
            {"id": "goal-1", "delete": True},
        )

        self.assertTrue(skill_result["success"])
        self.assertTrue(skill_result["data"]["changed"])
        self.assertTrue(goal_result["success"])
        self.assertTrue(goal_result["data"]["changed"])
        self.assertNotIn("skill-1", self.plugin.data["skill_growth"]["skills"])
        self.assertEqual([], self.plugin.data["personal_goals"])
