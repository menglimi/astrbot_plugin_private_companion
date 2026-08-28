from __future__ import annotations

import unittest
from typing import Any

from astrbot_plugin_private_companion.proactive import ProactiveMixin


class _ProactiveIdentityHarness(ProactiveMixin):
    def __init__(self) -> None:
        self.data = {"users": {}}

    @staticmethod
    def _normalize_private_identity_id(value: Any) -> str:
        text = str(value or "").strip()
        return text if ":" not in text else ""

    @staticmethod
    def _canonical_private_user_id(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _req036_proactive_private_allowed(_user: dict[str, Any]) -> bool:
        return True

    @staticmethod
    def _is_bot_self_user_id(_user_id: str) -> bool:
        return False


class ActiveIdentityAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = _ProactiveIdentityHarness()

    def test_active_delivery_requires_stamped_platform_account_binding(self) -> None:
        base = {
            "user_id": "10001",
            "identity_subject_id": "10001",
            "identity_platform_kind": "onebot",
        }
        self.assertFalse(self.harness._proactive_identity_binding_is_verified("10001", base))

        verified = {
            **base,
            "identity_adapter_instance_id": "onebot:main",
        }
        self.assertTrue(self.harness._proactive_identity_binding_is_verified("10001", verified))

    def test_active_delivery_rejects_storage_subject_mismatch(self) -> None:
        user = {
            "user_id": "10002",
            "identity_subject_id": "10001",
            "identity_platform_kind": "onebot",
            "identity_adapter_instance_id": "onebot:main",
        }
        self.assertFalse(self.harness._proactive_identity_binding_is_verified("10002", user))

    def test_scoped_transport_record_uses_stamped_subject(self) -> None:
        user = {
            "user_id": "onebot:10001:abcdef0123456789",
            "identity_subject_id": "10001",
            "identity_platform_kind": "onebot",
            "identity_bot_id": "bot-main",
        }
        self.assertTrue(self.harness._proactive_identity_binding_is_verified(user["user_id"], user))

    def test_duplicate_exact_binding_is_not_allowed_to_send(self) -> None:
        first = {
            "user_id": "10001",
            "identity_subject_id": "10001",
            "identity_platform_kind": "onebot",
            "identity_adapter_instance_id": "onebot:main",
            "manual_enabled": True,
        }
        second = {
            **first,
            "user_id": "onebot:10001:abcdef0123456789",
        }
        self.harness.data["users"] = {"10001": first, second["user_id"]: second}

        self.assertFalse(self.harness._user_enabled_for_proactive("10001", first))
        self.assertFalse(self.harness._user_enabled_for_proactive(second["user_id"], second))


if __name__ == "__main__":
    unittest.main()
