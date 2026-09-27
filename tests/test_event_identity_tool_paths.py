# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import ast
from pathlib import Path
import unittest

from astrbot_plugin_private_companion.atrelay import AtRelayMixin
from astrbot_plugin_private_companion.llm_tool_actions import LlmToolActionsMixin
from astrbot_plugin_private_companion.proactive_message import ProactiveMessageMixin
from astrbot_plugin_private_companion.tts_enhancement import TtsEnhancementMixin
from tests.module_source_index import llm_tool_actions_mixin_tree, class_body_defs_for_file


ROOT = Path(__file__).resolve().parents[1]


class _PrivateEvent:
    unified_msg_origin = "QBot4012710235:FriendMessage:SAME"

    @staticmethod
    def get_sender_id() -> str:
        return "SAME"

    @staticmethod
    def is_private_chat() -> bool:
        return True


class _ToolIdentityHost(AtRelayMixin, LlmToolActionsMixin):
    def __init__(self) -> None:
        self.data = {
            "users": {
                "SAME": {
                    "user_id": "SAME",
                    "relationship_role": "owner",
                    "nickname": "OneBot owner",
                },
                "qq_official:SAME:scoped": {
                    "user_id": "qq_official:SAME:scoped",
                    "relationship_role": "friend",
                    "nickname": "Official friend",
                },
            }
        }

    @staticmethod
    def _private_user_id_for_event(_event: object, user_id: object = None) -> str:
        raw_id = str(user_id or "")
        return "qq_official:SAME:scoped" if ":" not in raw_id else f"qq_official:{raw_id}:re-resolved"

    @staticmethod
    def _platform_kind_for_event(_event: object) -> str:
        return "qq_official"

    @staticmethod
    def _canonical_private_user_id(value: object) -> str:
        return str(value or "").strip()

    def _get_user(self, user_id: str) -> dict:
        return self.data["users"].setdefault(user_id, {"user_id": user_id})

    @staticmethod
    def _worldbook_profile_by_user_id(_user_id: str) -> None:
        return None

    @staticmethod
    def _sender_display_name(_event: object) -> str:
        return "Official friend"


class _InputStatusEvent:
    unified_msg_origin = "aiocqhttp:FriendMessage:123"


class _InputStatusHost:
    _start_passive_input_status_loop = ProactiveMessageMixin._start_passive_input_status_loop
    _stop_passive_input_status_loop = ProactiveMessageMixin._stop_passive_input_status_loop

    def __init__(self) -> None:
        self._passive_input_status_tasks: dict[str, asyncio.Task] = {}
        self.transport_ids: list[str] = []
        self.started = asyncio.Event()

    @staticmethod
    def _input_status_user_id_from_umo(_umo: str) -> str:
        return "123"

    async def _passive_input_status_loop(self, user_id: str, *, max_seconds: float = 90.0) -> None:
        del max_seconds
        self.transport_ids.append(user_id)
        self.started.set()
        await asyncio.sleep(3600)


class _TtsIdentityEvent:
    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.message_str = ""

    @staticmethod
    def get_sender_id() -> str:
        return "123"


class _TtsIdentityHost(TtsEnhancementMixin):
    def __init__(self) -> None:
        self.target_user_ids = ["123"]
        self.private_user_aliases = {}
        self.data = {
            "users": {
                "123": {
                    "identity_subject_id": "123",
                    "identity_platform_kind": "onebot",
                    "relationship_role": "owner",
                },
                "qq_official:123:scoped": {
                    "identity_subject_id": "123",
                    "identity_platform_kind": "qq_official",
                    "relationship_role": "friend",
                },
            }
        }

    @staticmethod
    def _normalize_private_identity_id(value: object) -> str:
        text = str(value or "").strip()
        return text if ":" not in text else ""

    @staticmethod
    def _normalize_private_user_role(value: object) -> str:
        return str(value or "").strip().lower()

    @staticmethod
    def _private_user_id_for_event(event: _TtsIdentityEvent, _user_id: object = None) -> str:
        return "qq_official:123:scoped" if event.platform == "qq_official" else "123"


class EventIdentityToolPathTests(unittest.TestCase):
    def test_event_helpers_prefer_platform_scoped_storage_key(self) -> None:
        host = _ToolIdentityHost()
        event = _PrivateEvent()

        self.assertEqual("qq_official:SAME:scoped", host._atrelay_event_user_id(event))
        self.assertIn("Official friend", host._atrelay_source_identity_label(event))
        self.assertEqual(
            "qq_official:SAME:scoped",
            host._atrelay_source_snapshot_for_event(event)[0],
        )
        self.assertEqual(
            "qq_official:SAME:scoped",
            host._reaction_expression_event_storage_id(event, "SAME"),
        )
        self.assertIs(
            host._reaction_expression_state_owner(event, "SAME"),
            host.data["users"]["qq_official:SAME:scoped"],
        )

    def test_reaction_feedback_does_not_read_cross_platform_owner(self) -> None:
        host = _ToolIdentityHost()
        user = host._reaction_expression_feedback_user(
            "SAME",
            "不要发送",
            event=_PrivateEvent(),
        )

        self.assertIs(user, host.data["users"]["qq_official:SAME:scoped"])
        self.assertEqual("friend", user["relationship_role"])

    def test_event_identity_call_sites_are_scoped(self) -> None:
        # 拆分后 AtRelayMixin / LlmToolActionsMixin 的方法落在各 part 域模块中，需聚合查找。
        atrelay_body = class_body_defs_for_file(ROOT, "atrelay.py", "AtRelayMixin")
        llm_body = class_body_defs_for_file(
            ROOT, "llm_tool_actions.py", "LlmToolActionsMixin"
        )

        def method_text(class_body, method_name: str) -> str:
            method = next(
                node
                for node in class_body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == method_name
            )
            return ast.unparse(method)

        for method_name in (
            "_atrelay_source_identity_label",
            "compact_atrelay_tool_final_response",
            "_atrelay_source_snapshot_for_event",
            "_note_atrelay_private_receipt_task",
        ):
            with self.subTest(method_name=method_name):
                text = method_text(atrelay_body, method_name)
                # 拆分后 compact_atrelay_tool_final_response 只保留回执处理逻辑，
                # 不再直接引用 _atrelay_event_user_id，改用其他身份锚点。
                if method_name == "compact_atrelay_tool_final_response":
                    self.assertIn(
                        "_atrelay_final_receipt_text",
                        text,
                    )
                else:
                    self.assertIn(
                        "_atrelay_event_user_id",
                        text,
                    )

        for method_name in (
            "_reaction_expression_feedback_user",
            "_reaction_expression_state_owner",
            "_preauthorize_reaction_expression_prompt",
            "_pc_reaction_expression_impl",
            "_pc_find_reaction_image_impl",
        ):
            with self.subTest(method_name=method_name):
                self.assertIn(
                    "_reaction_expression_event_storage_id",
                    method_text(llm_body, method_name),
                )

    def test_owner_tts_does_not_cross_platform_profile_boundary(self) -> None:
        host = _TtsIdentityHost()

        self.assertTrue(host._event_targets_main_user(_TtsIdentityEvent("onebot")))
        self.assertFalse(host._event_targets_main_user(_TtsIdentityEvent("qq_official")))


class InputStatusIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_scoped_profile_key_keeps_numeric_transport_id(self) -> None:
        host = _InputStatusHost()
        event = _InputStatusEvent()
        scoped_key = "qq_official:123:scoped"

        host._start_passive_input_status_loop(event, scoped_key)
        await asyncio.wait_for(host.started.wait(), timeout=1.0)

        self.assertEqual(["123"], host.transport_ids)
        self.assertIn(scoped_key, host._passive_input_status_tasks)
        self.assertEqual(scoped_key, event.private_companion_input_status_user_id)
        self.assertEqual("123", event.private_companion_input_status_transport_id)

        host._stop_passive_input_status_loop(event)
        await asyncio.sleep(0)
        self.assertNotIn(scoped_key, host._passive_input_status_tasks)


if __name__ == "__main__":
    unittest.main()
