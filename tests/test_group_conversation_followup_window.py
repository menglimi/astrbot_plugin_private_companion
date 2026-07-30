import unittest
from unittest.mock import patch

from astrbot_plugin_private_companion.event_dispatch import EventDispatchMixin


class _FollowupHarness(EventDispatchMixin):
    enable_group_scene_awareness = True
    enable_group_conversation_followup = True
    group_conversation_followup_seconds = 40
    group_conversation_followup_max_turns = 1
    bot_name = "Bot"


class GroupConversationFollowupWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_reply_restarts_followup_window(self):
        harness = _FollowupHarness()
        group = {"message_count": 1, "recent_messages": []}

        with patch("astrbot_plugin_private_companion.event_dispatch._now_ts", return_value=100.0):
            harness._mark_group_bot_conversation(
                group,
                "user-1",
                "用户",
                active=True,
                text="你在干嘛呀",
            )

        active = group["active_bot_conversation"]
        self.assertEqual(140.0, active["expires_at"])

        self.assertTrue(
            harness._refresh_group_bot_conversation_after_reply(
                group,
                "user-1",
                now=139.0,
            )
        )
        self.assertEqual(139.0, active["last_ts"])
        self.assertEqual(179.0, active["expires_at"])

        with patch("astrbot_plugin_private_companion.event_dispatch._now_ts", return_value=145.0):
            continued = await harness._group_message_is_bot_continuation(
                group,
                "user-1",
                "用户",
                {"talking_to": "group", "trigger": "plain"},
                "你准备几点睡呀？",
                allow_llm=False,
            )

        self.assertTrue(continued)

    def test_confirmed_reply_does_not_replace_another_senders_anchor(self):
        harness = _FollowupHarness()
        group = {
            "message_count": 2,
            "active_bot_conversation": {
                "sender_id": "user-2",
                "last_ts": 100.0,
                "expires_at": 140.0,
                "contextual_followups": 0,
            },
        }

        refreshed = harness._refresh_group_bot_conversation_after_reply(
            group,
            "user-1",
            now=120.0,
        )

        self.assertFalse(refreshed)
        self.assertEqual(100.0, group["active_bot_conversation"]["last_ts"])
        self.assertEqual(140.0, group["active_bot_conversation"]["expires_at"])


if __name__ == "__main__":
    unittest.main()
