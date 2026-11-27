"""Derived identity: the account and persona decide the memory owner.

These values are pinned into ``StagingConfiguration``, which is fingerprinted on
disk. So the tests that matter are not "does it produce a nice string" but:

1. **Stability.** The same inputs must derive the same ids every time, forever.
   A derivation that drifts between two calls reads as a profile conflict, and
   the operator sees "the pinned settings changed" with nothing having changed.
2. **Separation.** Two accounts, or one account with two personas, must not land
   on one role. That is the whole reason for deriving instead of using the old
   shared constants -- and getting it wrong silently merges two people's memories.
3. **Refusal.** The bot's own account must not be accepted as the conversation
   target. That mistake used to be catchable by eye in a config form; now it has
   to be caught here.
"""

import unittest

from astrbot_host.identity_derivation import (
    LEGACY_BOT_ID,
    LEGACY_ROLE_ID,
    derive_bot_id,
    derive_role_id,
    derived_identity,
    derive_core_target_id,
    normalize_persona_id,
    persona_label,
    role_display_name,
)
from astrbot_host.settings import HostControlError


def identity(**overrides):
    values = {"platform_id": "default", "account_id": "10002", "participant_id": "10001"}
    values.update(overrides)
    return derived_identity(**values)


class DerivationTests(unittest.TestCase):
    def test_the_derivation_is_stable_across_calls(self):
        """Two calls with equal inputs must be equal, or a pin looks like a conflict."""
        first = identity(persona_id="晚星")
        for _ in range(8):
            self.assertEqual(identity(persona_id="晚星"), first)

    def test_two_accounts_do_not_share_a_role(self):
        left = identity(account_id="10002", persona_id="晚星")
        right = identity(account_id="10003", persona_id="晚星")
        self.assertNotEqual(left["role_id"], right["role_id"])
        self.assertNotEqual(left["bot_id"], right["bot_id"])

    def test_one_account_with_two_personas_keeps_two_roles(self):
        left = identity(persona_id="晚星")
        right = identity(persona_id="朝雾")
        self.assertEqual(left["bot_id"], right["bot_id"], "the bot is the account, not the persona")
        self.assertNotEqual(left["role_id"], right["role_id"])

    def test_the_derived_ids_are_not_the_shared_legacy_constants(self):
        """The old constants are kept only so existing pins keep reading back."""
        value = identity(persona_id="晚星")
        self.assertNotEqual(value["bot_id"], LEGACY_BOT_ID)
        self.assertNotEqual(value["role_id"], LEGACY_ROLE_ID)

    def test_an_empty_persona_still_yields_a_role(self):
        """No persona selected means 'follow the conversation', not 'no memory owner'."""
        value = identity(persona_id=None)
        self.assertEqual(value["persona_id"], "")
        self.assertTrue(value["role_id"].startswith("role-"))
        self.assertNotEqual(value["role_id"], identity(persona_id="晚星")["role_id"])

    def test_the_role_id_does_not_echo_the_persona_name(self):
        """The role id is a storage key; a persona is a display name."""
        value = identity(persona_id="晚星")
        self.assertNotIn("晚星", value["role_id"])

    def test_the_role_id_is_bounded(self):
        value = identity(persona_id="人" * 120)
        self.assertLessEqual(len(value["role_id"]), 64)

    def test_the_conversation_target_may_not_be_the_bot(self):
        """Sending to yourself is never the intent, and it used to be visible in a form."""
        with self.assertRaises(HostControlError) as caught:
            identity(account_id="10002", participant_id="10002")
        self.assertEqual(caught.exception.code, "host_identity_invalid")

    def test_an_invalid_native_account_is_refused(self):
        for bad in ("", "   ", "alice!", "10002 ", ".", "..", "a..b", ".hidden", "trail.",
                    "a/b", "a\\b", "a:b", "@@@", "---", None, 10002):
            with self.subTest(value=bad):
                with self.assertRaises(HostControlError):
                    derive_bot_id(bad)

    def test_weixin_oc_account_with_at_and_dot_is_accepted_and_stable(self):
        first = identity(platform_id="wx-main", account_id="wx-main",
                         participant_id="o9cq8043@im.wechat", persona_id="晚星")
        self.assertEqual(first["participant_id"], "o9cq8043@im.wechat")
        self.assertEqual(first, identity(platform_id="wx-main", account_id="wx-main",
                                         participant_id="o9cq8043@im.wechat", persona_id="晚星"))
        self.assertEqual(derive_bot_id("bot.01@im.wechat"), "bot-bot.01@im.wechat")
        self.assertTrue(derive_role_id("bot.01@im.wechat", "晚星").startswith("role-bot.01@im.wechat-"))

    def test_an_opaque_official_account_is_accepted(self):
        self.assertTrue(derive_bot_id("607097777B7E7FE3E162A8F56825F8521").startswith("bot-"))

    def test_unicode_native_account_is_accepted_with_an_ascii_storage_id(self):
        value = identity(account_id="官方实例", participant_id="用户一")
        self.assertEqual(value["account_id"], "官方实例")
        self.assertTrue(value["bot_id"].isascii())
        self.assertTrue(value["role_id"].isascii())

    def test_unicode_native_binding_gets_a_stable_ascii_target_id(self):
        target = derive_core_target_id("官方平台", "官方实例", "用户一")
        self.assertEqual(target, derive_core_target_id("官方平台", "官方实例", "用户一"))
        self.assertRegex(target, r"^setup-core-[0-9a-f]{24}$")

    def test_a_missing_platform_is_refused(self):
        for bad in ("", "   ", None):
            with self.subTest(value=bad):
                with self.assertRaises(HostControlError):
                    identity(platform_id=bad)

    def test_the_platform_id_is_trimmed_not_echoed_verbatim(self):
        self.assertEqual(identity(platform_id=" default ")["platform_id"], "default")


class PersonaNormalizationTests(unittest.TestCase):
    def test_the_none_sentinel_means_no_persona(self):
        """AstrBot uses '[%None]' for 'this conversation has no persona'."""
        self.assertEqual(normalize_persona_id("[%None]"), "")
        self.assertEqual(derive_role_id("10002", "[%None]"), derive_role_id("10002", ""))

    def test_whitespace_does_not_create_a_second_role(self):
        self.assertEqual(derive_role_id("10002", " 晚星 "), derive_role_id("10002", "晚星"))

    def test_a_multi_line_or_overlong_persona_is_refused(self):
        for bad in ("晚\n星", "人" * 121):
            with self.subTest(value=bad):
                with self.assertRaises(HostControlError):
                    normalize_persona_id(bad)

    def test_a_non_string_persona_is_refused_rather_than_stringified(self):
        with self.assertRaises(HostControlError):
            normalize_persona_id(1234)

    def test_the_label_falls_back_to_the_conversation_persona(self):
        self.assertEqual(persona_label(""), "随会话当前人格")
        self.assertEqual(persona_label("晚星"), "晚星")

    def test_role_display_name_removes_only_channel_suffixes_with_optional_spaces(self):
        for value in ("星缘-私聊", "星缘 - 私聊", "星缘_群聊", "星缘 _ 群聊",
                      "星缘·私聊", "星缘 · 群聊", "星缘－群聊"):
            with self.subTest(value=value):
                self.assertEqual(role_display_name(value), "星缘")
        self.assertEqual(role_display_name("深夜-来信"), "深夜-来信")

    def test_generated_role_display_name_uses_normalized_persona_labels(self):
        self.assertEqual(role_display_name("role-account-persona", ["星缘 - 私聊"]), "星缘")
        self.assertEqual(role_display_name("role-account-persona", ["星缘·私聊", "星缘·群聊"]), "星缘")


if __name__ == "__main__":
    unittest.main()
