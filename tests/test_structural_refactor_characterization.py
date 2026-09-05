# -*- coding: utf-8 -*-
"""Characterization tests for the memory/tool seams being extracted.

These deliberately assert legacy observable values rather than an idealized API.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.user_memory import UserMemoryMixin


class _MemoryHost(UserMemoryMixin):
    max_companion_memory_items = 2


class _PrivateEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:owner"

    @staticmethod
    def is_private_chat() -> bool:
        return True

    @staticmethod
    def get_sender_id() -> str:
        return "owner"


class _InteractionHost(LlmToolActionsMixin):
    enable_cross_user_memory_bridge = True
    cross_user_memory_owner_only = True
    target_platform = "aiocqhttp"

    def __init__(self) -> None:
        self.data = {
            "users": {
                "42": {
                    "user_id": "42",
                    "enabled": True,
                    "nickname": "阿青",
                }
            },
            "worldbook_member_profiles": {},
        }

    @staticmethod
    def _event_permission_identity_id(_event: object) -> str:
        return "owner"

    @staticmethod
    def _is_private_companion_owner_user_id(user_id: str) -> bool:
        return user_id == "owner"

    @staticmethod
    def _is_configured_admin_user_id(_user_id: str) -> bool:
        return False

    @staticmethod
    def _configured_target_ids() -> list[str]:
        return ["42"]

    @staticmethod
    def _canonical_private_user_id(user_id: str) -> str:
        return user_id

    @staticmethod
    def _is_target_private_user(_user_id: str, _user: dict) -> bool:
        return True

    async def _interaction_query_read_history(self, umo: str, *, limit: int, hours: int):
        self.read_call = (umo, limit, hours)
        return [{"role": "user", "content": "最近在学 Rust"}]

    @staticmethod
    def _format_history_item_for_summary(item: dict) -> str:
        return f"user: {item['content']}"


class StructuralRefactorCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def test_memory_cleanup_mutates_same_persistence_shape_and_deduplicates(self) -> None:
        host = _MemoryHost()
        user = {
            "companion_memory": {
                "items": [
                    {"text": " 喜欢草莓蛋糕 ", "weight": 1, "created_ts": 900.0, "extra": "kept"},
                    {"text": "喜欢草莓蛋糕", "weight": 9, "created_ts": 800.0},
                    {"text": "会写 Python", "weight": 3, "created_ts": 950.0},
                    {"text": "", "weight": 5},
                ]
            }
        }
        with patch("astrbot_plugin_private_companion.user_memory._now_ts", return_value=1000.0):
            result = host._cleanup_companion_memory_items(user)

        self.assertIs(result, user["companion_memory"]["items"])
        self.assertEqual(["会写 Python", "喜欢草莓蛋糕"], [item["text"] for item in result])
        self.assertEqual("kept", result[1]["extra"])
        self.assertEqual(900.0, result[1]["created_ts"])

    def test_memory_relevance_keeps_weight_then_timestamp_order_without_hint(self) -> None:
        host = _MemoryHost()
        user = {"companion_memory": {"items": [
            {"text": "养了一只猫", "weight": 1, "created_ts": 990.0},
            {"text": "在学 Python 爬虫", "weight": 3, "created_ts": 980.0},
        ]}}
        with patch("astrbot_plugin_private_companion.user_memory._now_ts", return_value=1000.0):
            result = host._companion_memory_relevant_items(user, limit=1)
        self.assertEqual(["在学 Python 爬虫"], [item["text"] for item in result])

    async def test_interaction_query_aliases_clamps_and_preserves_response_contract(self) -> None:
        host = _InteractionHost()
        payload = json.loads(await host._pc_query_interaction_impl(
            _PrivateEvent(),
            type="friend",
            user="阿青",
            hours=99999,
            limit=1,
        ))

        self.assertEqual("success", payload["status"])
        self.assertEqual("private", payload["scope"])
        self.assertEqual("42", payload["target"]["user_id"])
        self.assertEqual(720, payload["hours"])
        self.assertEqual(["user: 最近在学 Rust"], payload["recent_lines"])
        self.assertEqual(("aiocqhttp:FriendMessage:42", 5, 720), host.read_call)

    async def test_interaction_query_invalid_scope_falls_back_to_private(self) -> None:
        host = _InteractionHost()
        payload = json.loads(await host._pc_query_interaction_impl(
            _PrivateEvent(), scope="unexpected", hint="无人匹配"
        ))
        self.assertEqual({
            "status": "not_found",
            "message": "没有找到匹配的私聊对象",
            "hint": "无人匹配",
        }, payload)


if __name__ == "__main__":
    unittest.main()
