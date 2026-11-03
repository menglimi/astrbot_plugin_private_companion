"""跨窗口递话的授权判定：谁可以发给谁。"""

import unittest

from companion_v2.conversation.relay_policy import (
    ALLOWED, DENIED_DISABLED, DENIED_GROUP, DENIED_MEMBER_DISABLED,
    DENIED_MEMBER_SENDER, DENIED_SELF, DENIED_TARGET,
    DENIED_TEXT_EMPTY, DENIED_TEXT_TOO_LONG, RelayRequest, decide)


def ask(destination_id, text="你好", *, kind="private", source_id="1001"):
    return RelayRequest(kind, destination_id, text, source_id)


class CoreAdminRelayTests(unittest.TestCase):
    """核心管理员：可以让 bot 帮忙私聊递话，但只发白名单目标，且要确认。"""

    def test_core_admin_can_relay_to_a_whitelisted_private_target(self):
        decision = decide(ask("2002"), actor_is_core=True, core_target_id="1001",
                          allowed_targets=("2002", "3003"))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.code, ALLOWED)
        self.assertTrue(decision.needs_confirm)
        self.assertEqual(decision.audience, "admin_target")

    def test_core_admin_cannot_relay_to_a_target_outside_the_whitelist(self):
        decision = decide(ask("9999"), actor_is_core=True, core_target_id="1001",
                          allowed_targets=("2002",))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, DENIED_TARGET)

    def test_core_admin_relay_needs_no_confirm_when_configured_off(self):
        decision = decide(ask("2002"), actor_is_core=True, core_target_id="1001",
                          allowed_targets=("2002",), require_confirm=False)
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.needs_confirm)


class MemberRelayTests(unittest.TestCase):
    """其他用户：只能给核心管理员递话。"""

    def test_member_can_relay_to_the_core_admin(self):
        decision = decide(ask("1001", source_id="5005"), actor_is_core=False,
                          core_target_id="1001", allowed_targets=("2002",))
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.audience, "owner")
        self.assertFalse(decision.needs_confirm)

    def test_member_cannot_relay_to_anyone_else(self):
        for destination in ("2002", "3003"):
            decision = decide(ask(destination, source_id="5005"), actor_is_core=False,
                              core_target_id="1001", allowed_targets=("2002", "3003"))
            self.assertFalse(decision.allowed)
            self.assertEqual(decision.code, DENIED_TARGET)

    def test_member_without_a_configured_admin_target_is_rejected(self):
        decision = decide(ask("1001", source_id="5005"), actor_is_core=False, core_target_id="")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, DENIED_TARGET)


class RelayBoundaryTests(unittest.TestCase):
    """两类用户共用的边界。"""

    def test_disabling_outbound_relay_does_not_block_relay_to_the_core_admin(self):
        core = decide(ask("2002"), actor_is_core=True, core_target_id="1001",
                      allowed_targets=("2002",), enabled=False)
        self.assertFalse(core.allowed)
        self.assertEqual(core.code, DENIED_DISABLED)
        member = decide(ask("1001", source_id="5005"), actor_is_core=False,
                        core_target_id="1001", enabled=False)
        self.assertTrue(member.allowed)

    def test_member_relay_has_its_own_switch_sender_list_and_length_limit(self):
        disabled = decide(ask("1001", source_id="5005"), actor_is_core=False,
                          core_target_id="1001", member_relay_enabled=False)
        self.assertFalse(disabled.allowed)
        self.assertEqual(disabled.code, DENIED_MEMBER_DISABLED)
        blocked = decide(ask("1001", source_id="5005"), actor_is_core=False,
                         core_target_id="1001", actor_id="5005",
                         member_relay_allowed_senders=("6006",))
        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.code, DENIED_MEMBER_SENDER)
        too_long = decide(ask("1001", "长" * 21, source_id="5005"), actor_is_core=False,
                          core_target_id="1001", member_relay_max_chars=20)
        self.assertEqual(too_long.code, DENIED_TEXT_TOO_LONG)

    def test_group_destination_requires_its_own_whitelist(self):
        allowed = decide(ask("5005", kind="group"), actor_is_core=True,
                         core_target_id="1001", allowed_groups=("5005",))
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.audience, "admin_group")
        denied = decide(ask("5005", kind="group"), actor_is_core=True,
                        core_target_id="1001", allowed_targets=("5005",))
        self.assertFalse(denied.allowed)
        self.assertEqual(denied.code, DENIED_GROUP)

    def test_member_cannot_use_a_group_destination(self):
        decision = decide(ask("5005", kind="group"), actor_is_core=False,
                          core_target_id="1001", allowed_groups=("5005",))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, DENIED_GROUP)

    def test_blank_and_overlong_text_are_rejected(self):
        self.assertEqual(decide(ask("2002", "   "), actor_is_core=True,
                                core_target_id="1001", allowed_targets=("2002",)).code,
                         DENIED_TEXT_EMPTY)
        self.assertEqual(decide(ask("2002", "长" * 501), actor_is_core=True,
                                core_target_id="1001", allowed_targets=("2002",)).code,
                         DENIED_TEXT_TOO_LONG)

    def test_relaying_to_the_current_window_is_rejected(self):
        decision = decide(ask("1001", source_id="1001"), actor_is_core=True,
                          core_target_id="7777", allowed_targets=("1001",))
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, DENIED_SELF)


if __name__ == "__main__":
    unittest.main()
