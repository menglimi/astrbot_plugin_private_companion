# -*- coding: utf-8 -*-
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import asyncio
import unittest

from quart import Quart

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin
from astrbot_plugin_private_companion.final_response_persistence import FinalResponsePersistenceMixin
from astrbot_plugin_private_companion.group_observation import GroupObservationMixin
from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin
from astrbot_plugin_private_companion.page_api import PrivateCompanionPageApi


class _HistoryHarness(GroupObservationMixin, EventDispatchMixin):
    max_group_recent_messages = 3
    group_air_guard_window_seconds = 180


class _ConfirmedHarness(
    FinalResponsePersistenceMixin,
    GroupObservationMixin,
    EventDispatchMixin,
):
    max_group_recent_messages = 2

    def __init__(self) -> None:
        self.group = {
            "group_id": "group-a",
            "recent_bot_replies": [
                {"ts": 1, "sender_id": "legacy-user", "text": "legacy-1"},
                {"ts": 2, "sender_id": "legacy-user", "text": "legacy-2"},
            ],
        }

    @staticmethod
    def _extract_group_id_from_event(_event) -> str:
        return "group-a"

    def _get_group(self, _group_id: str) -> dict:
        return self.group

    @staticmethod
    def _feature_enabled_or_temp_unlocked(_key: str) -> bool:
        return True


class _SendEvent:
    def __init__(self) -> None:
        self.sent: list[object] = []

    @staticmethod
    def plain_result(text: str) -> tuple[str, str]:
        return ("plain", text)

    async def send(self, result) -> None:
        self.sent.append(result)


class _SendHarness(InteractionUtilsMixin):
    enable_proactive_quote_trigger_message = False

    def __init__(self, recalled: bool = False) -> None:
        self.recalled = recalled

    async def _should_cancel_reply_for_missing_or_recalled_trigger(self, *_args):
        return "recalled" if self.recalled else ""


class _PagePlugin:
    def __init__(self) -> None:
        self._data_lock = asyncio.Lock()
        self.data = {
            "groups": {
                "group-a": {
                    "group_id": "group-a",
                    "enabled": True,
                    "recent_messages": [{"text": "member"}],
                    "recent_bot_replies": [
                        {"ts": 1, "sender_id": "legacy-user", "text": "bot", "secret": "drop"}
                    ],
                    "active_bot_conversation": {"sender_id": "legacy-user"},
                }
            }
        }
        self.saved_sections: set[str] = set()

    def _get_group(self, group_id: str) -> dict:
        return self.data["groups"][group_id]

    def _save_data_sync(self, *, sections=None) -> None:
        self.saved_sections.update(sections or ())


class GroupBotHistoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.app = Quart(__name__)

    def test_member_and_bot_histories_share_the_persona_runtime_limit(self) -> None:
        harness = _HistoryHarness()
        group = {
            "recent_messages": [{"text": str(index)} for index in range(5)],
            "recent_bot_replies": [{"text": f"bot-{index}"} for index in range(4)],
        }

        harness._trim_group_history_lists(group)

        self.assertEqual(["2", "3", "4"], [item["text"] for item in group["recent_messages"]])
        self.assertEqual(["bot-1", "bot-2", "bot-3"], [item["text"] for item in group["recent_bot_replies"]])

    def test_bot_record_has_explicit_target_kind_and_delivery_identity(self) -> None:
        harness = _HistoryHarness()
        group: dict = {}

        record = harness._record_group_bot_reply(
            group,
            text="已发送",
            reply_to_id="user-a",
            kind="passive_reply",
            talking_to_bot=True,
            ts=123.0,
            delivery_id="delivery-a",
        )

        self.assertEqual(
            {
                "ts": 123.0,
                "text": "已发送",
                "reply_to_id": "user-a",
                "kind": "passive_reply",
                "talking_to_bot": True,
                "delivery_id": "delivery-a",
            },
            record,
        )

    def test_air_guard_filter_is_a_pure_time_window_view(self) -> None:
        harness = _HistoryHarness()
        group = {
            "recent_bot_replies": [
                {"ts": 700, "text": "old"},
                {"ts": 850, "text": "recent-a"},
                {"ts": 900, "text": "recent-b"},
            ]
        }
        before = deepcopy(group)

        visible = harness._group_air_guard_trim_bot_replies(group, now=1000)

        self.assertEqual(["recent-a", "recent-b"], [item["text"] for item in visible])
        self.assertEqual(before, group)

    def test_confirmed_passive_group_reply_is_recorded_even_without_active_dialogue(self) -> None:
        harness = _ConfirmedHarness()
        event = SimpleNamespace(
            is_private_chat=lambda: False,
            get_sender_id=lambda: "user-a",
            private_companion_group_scene={"talking_to": "group"},
        )

        updated = harness._record_confirmed_group_bot_state_locked(
            event,
            response_text="普通群回复",
            now=100.0,
            delivery_id="delivery-passive",
        )

        self.assertEqual({"groups"}, updated)
        self.assertEqual(2, len(harness.group["recent_bot_replies"]))
        record = harness.group["recent_bot_replies"][-1]
        self.assertEqual("user-a", record["reply_to_id"])
        self.assertEqual("passive_reply", record["kind"])
        self.assertEqual("delivery-passive", record["delivery_id"])
        self.assertNotIn("last_bot_reply", harness.group["active_bot_conversation"])

    async def test_optional_media_reply_reports_send_and_recall_outcomes(self) -> None:
        event = _SendEvent()

        sent = await _SendHarness()._reply_with_optional_media(event, "hello")
        recalled = await _SendHarness(recalled=True)._reply_with_optional_media(event, "ignored")
        empty = await _SendHarness()._reply_with_optional_media(event, "")

        self.assertTrue(sent)
        self.assertFalse(recalled)
        self.assertFalse(empty)
        self.assertEqual([("plain", "hello")], event.sent)

    async def test_reset_controls_preserve_histories_but_clear_removes_all_context(self) -> None:
        plugin = _PagePlugin()
        api = PrivateCompanionPageApi(plugin)
        api._group_summary = lambda group_id, _group: {"group_id": group_id}

        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": "group-a", "reset_interjection": True, "reset_atmosphere": True},
        ):
            reset_result = await api.update_group()

        group = plugin.data["groups"]["group-a"]
        self.assertTrue(reset_result["success"])
        self.assertEqual([{"text": "member"}], group["recent_messages"])
        self.assertEqual("bot", group["recent_bot_replies"][0]["text"])
        self.assertEqual("legacy-user", group["active_bot_conversation"]["sender_id"])

        async with self.app.test_request_context(
            "/group/update",
            method="POST",
            json={"group_id": "group-a", "clear_observation": True},
        ):
            clear_result = await api.update_group()

        self.assertTrue(clear_result["success"])
        self.assertEqual([], group["recent_messages"])
        self.assertEqual([], group["recent_bot_replies"])
        self.assertEqual({}, group["active_bot_conversation"])

    def test_page_projection_sanitizes_bot_history_and_reads_legacy_target(self) -> None:
        plugin = _PagePlugin()
        api = PrivateCompanionPageApi(plugin)

        projected = api._group_page_recent_bot_replies(plugin.data["groups"]["group-a"])

        self.assertEqual("legacy-user", projected[0]["reply_to_id"])
        self.assertEqual("bot_reply", projected[0]["kind"])
        self.assertNotIn("secret", projected[0])


if __name__ == "__main__":
    unittest.main()
