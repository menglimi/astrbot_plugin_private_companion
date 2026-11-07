"""Feishu/Lark is a first-class native platform, not a changed QQ account."""

import unittest
from types import SimpleNamespace as NS

from tests import astrbot_data_root  # noqa: F401 - isolate the SDK before import
from astrbot_host.binding import event_identity
from astrbot_host.group_admission import group_trigger_facts
from companion_v2.adapters.astrbot.native import (
    event_account_id, native_user_subject_id, supported_platform_name,
)
from companion_v2.foundation.refs import ScopeKind


def lark_event(*, message_type="FriendMessage", self_id="ou_bot", sender="ou_user",
               parts=()):
    meta = NS(name="lark", id="feishu")
    message = NS(type=NS(value=message_type), self_id=self_id,
                 sender=NS(user_id=sender), raw_message={"message": list(parts)},
                 group_id="oc_group")
    return NS(platform_meta=meta, message_obj=message,
              unified_msg_origin=f"feishu:{message_type}:{sender}")


class LarkPlatformTests(unittest.TestCase):
    def test_lark_is_in_the_supported_platform_set(self):
        self.assertTrue(supported_platform_name("lark"))
        self.assertTrue(supported_platform_name("aiocqhttp"))

    def test_configured_instance_is_the_stable_account_key(self):
        event = lark_event()
        self.assertEqual(event_account_id(event), "feishu")

    def test_lark_subjects_use_their_own_namespace(self):
        self.assertEqual(native_user_subject_id("lark", "ou_123"), "lark-user:ou_123")
        with self.assertRaises(ValueError):
            native_user_subject_id("unknown", "ou_123")

    def test_binding_identity_does_not_use_the_bot_open_id_as_the_account(self):
        identity = event_identity(lark_event())
        self.assertEqual(identity, {"platform_id": "feishu", "account_id": "feishu",
                                    "participant_id": "ou_user"})

    def test_group_mention_can_name_the_bot_open_id(self):
        event = lark_event(message_type="GroupMessage", parts=[
            {"type": "at", "data": {"qq": "ou_bot"}}])
        facts = group_trigger_facts(event, "feishu")
        self.assertTrue(facts["mentioned"])
        self.assertFalse(facts["mentions_others"])


    def test_other_bundled_adapters_use_their_own_account_and_subject_keys(self):
        meta = NS(name="telegram", id="tg-main")
        message = NS(type=NS(value="FriendMessage"), self_id="bot-123",
                     sender=NS(user_id="42"), raw_message={"message": []}, group_id="")
        event = NS(platform_meta=meta, message_obj=message,
                   unified_msg_origin="tg-main:FriendMessage:42")
        self.assertEqual(event_account_id(event), "tg-main")
        self.assertEqual(native_user_subject_id("telegram", "42"), "telegram-user:42")
        self.assertEqual(event_identity(event),
                         {"platform_id": "tg-main", "account_id": "tg-main",
                          "participant_id": "42"})

    def test_default_platforms_stay_qq_only(self):
        from companion_v2.adapters.astrbot.native import DEFAULT_PLATFORM_NAMES
        self.assertEqual(DEFAULT_PLATFORM_NAMES,
                         {"aiocqhttp", "qq_official", "qq_official_webhook"})
        self.assertNotIn("lark", DEFAULT_PLATFORM_NAMES)


if __name__ == "__main__":
    unittest.main()
