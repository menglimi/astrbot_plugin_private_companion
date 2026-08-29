# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import time
import unittest

from quart import Quart

from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _AtmosphereHarness(GroupObservationMixin):
    @staticmethod
    def _filtered_group_recent_messages(group: dict) -> list[dict]:
        return list(group.get("recent_messages") or [])


class _AtmosphereApiPlugin(_AtmosphereHarness):
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {
            "groups": {
                "group-1": {
                    "group_id": "group-1",
                    "enabled": True,
                    "recent_messages": [],
                    "members": {"user-1": {"name": "群友"}},
                    "slang_terms": [{"term": "测试黑话"}],
                    "topic_threads": [{"title": "测试话题"}],
                    "group_episodes": [{"summary": "测试片段"}],
                    "relationship_edges": {"user-1:user-2": {"count": 2}},
                }
            }
        }

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"][group_id]

    @staticmethod
    def _save_data_sync(**_kwargs) -> None:
        return None


class GroupAtmosphereTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _AtmosphereHarness()

    def test_expired_negative_message_recovers_without_new_message(self) -> None:
        now = time.time()
        group = {
            "recent_messages": [{"sender_id": "1", "text": "别吵了", "ts": now - 13 * 60}],
            "atmosphere": {"mood": "紧绷"},
        }

        self.harness._update_group_atmosphere(group)

        self.assertEqual("平稳", group["atmosphere"]["mood"])
        self.assertEqual("安静", group["atmosphere"]["pace"])
        self.assertEqual(0, group["atmosphere"]["recent_count"])

    def test_single_tired_message_does_not_make_whole_group_tense(self) -> None:
        now = time.time()
        group = {"recent_messages": [{"sender_id": "1", "text": "今天有点累", "ts": now - 10}]}

        self.harness._update_group_atmosphere(group)

        self.assertEqual("平稳", group["atmosphere"]["mood"])

    def test_reset_ignores_old_messages_but_new_tension_can_be_detected(self) -> None:
        now = time.time()
        group = {
            "recent_messages": [{"sender_id": "1", "text": "别吵了", "ts": now - 60}],
            "atmosphere": {"mood": "紧绷", "reset_at": now - 30},
        }

        self.harness._update_group_atmosphere(group)
        self.assertEqual("平稳", group["atmosphere"]["mood"])

        group["recent_messages"].append({"sender_id": "2", "text": "不要吵架", "ts": time.time()})
        self.harness._update_group_atmosphere(group)
        self.assertEqual("紧绷", group["atmosphere"]["mood"])


class GroupAtmosphereApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = Quart(__name__)
        self.plugin = _AtmosphereApiPlugin()
        self.api = PrivateCompanionPageApi(self.plugin)
        self.api._group_summary = lambda group_id, group: {
            "group_id": group_id,
            "atmosphere": dict(group.get("atmosphere") or {}),
        }

    async def test_reset_only_changes_atmosphere_state(self) -> None:
        group = self.plugin.data["groups"]["group-1"]
        group["recent_messages"] = [{"sender_id": "1", "text": "别吵了", "ts": time.time() - 20}]
        before = {
            key: group[key]
            for key in ("recent_messages", "members", "slang_terms", "topic_threads", "group_episodes", "relationship_edges")
        }

        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": "group-1", "reset_atmosphere": True},
        ):
            result = await self.api.update_group()

        self.assertTrue(result["success"])
        self.assertEqual("平稳", group["atmosphere"]["mood"])
        for key, value in before.items():
            self.assertEqual(value, group[key])

    def test_page_refresh_recomputes_stale_snapshot(self) -> None:
        snapshot = {
            "recent_messages": [{"sender_id": "1", "text": "生气了", "ts": time.time() - 13 * 60}],
            "atmosphere": {"mood": "紧绷"},
        }

        refreshed = self.api._refresh_group_atmosphere_for_page(snapshot)

        self.assertEqual("平稳", refreshed["atmosphere"]["mood"])


if __name__ == "__main__":
    unittest.main()
