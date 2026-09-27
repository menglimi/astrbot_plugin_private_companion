# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from astrbot_plugin_private_companion import page_api as page_api_mod
from astrbot_plugin_private_companion import page_api_persona as page_api_persona_mod
from astrbot_plugin_private_companion import page_api_persona_config as page_api_persona_config_mod
from astrbot_plugin_private_companion import page_api_persona_flow as page_api_persona_flow_mod
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
        # update_personal_goal 已搬到 page_api_persona 模块，它读的是
        # page_api_persona.request；update_skill_growth 仍在宿主 page_api 模块。
        # 两者都要 patch，否则搬到 mixin 的方法会因 request 未替身而报
        # "Not within a request context"。
        with contextlib.ExitStack() as stack:
            for mod in (
                page_api_mod,
                page_api_persona_mod,
                page_api_persona_config_mod,
                page_api_persona_flow_mod,
            ):
                stack.enter_context(patch.object(mod, "request", fake_request))
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
