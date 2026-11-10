"""Saving the plugin model must not fail just because its Bot is offline.

Choosing a provider needs no live account; creating the shared installation does.
These tests pin that boundary: a missing connection defers the installation,
while a real configuration problem still fails the step.
"""

from types import SimpleNamespace
import unittest

from astrbot_host.setup_api import INIT_DEFERRED_CODES, deferred_initialization


def failure(code, reason="配置的 Bot 账号 default:123 当前未连接 AstrBot"):
    return SimpleNamespace(code=code, reason=reason)


class SetupInitDeferralTests(unittest.TestCase):
    def test_an_offline_bot_defers_the_shared_installation(self):
        for code in ("host_account_unavailable", "host_platform_unavailable"):
            deferred = deferred_initialization(failure(code))
            self.assertEqual((deferred["prepared"], deferred["init_deferred"], deferred["init_code"]),
                             (False, True, code))
            self.assertIn("default:123", deferred["init_reason"])

    def test_configuration_problems_are_not_deferred(self):
        for code in ("host_persona_unavailable", "host_account_changed", "host_mappings_conflict",
                     "host_conversation_changed", "host_must_stop", "host_control_failed"):
            self.assertIsNone(deferred_initialization(failure(code)), code)

    def test_success_and_unknown_failures_are_not_deferred(self):
        self.assertIsNone(deferred_initialization(None))
        self.assertIsNone(deferred_initialization(SimpleNamespace(code=None, reason="")))
        self.assertEqual(INIT_DEFERRED_CODES, frozenset(("host_account_unavailable", "host_platform_unavailable")))


if __name__ == "__main__":
    unittest.main()
