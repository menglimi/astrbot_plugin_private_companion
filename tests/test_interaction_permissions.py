# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from astrbot_plugin_private_companion.interaction_utils import InteractionUtilsMixin


class _Event:
    def __init__(self, sender_id: str, *, private: bool = True) -> None:
        self.sender_id = sender_id
        self.private = private

    def get_sender_id(self) -> str:
        return self.sender_id

    def is_private_chat(self) -> bool:
        return self.private


class _PermissionHarness(InteractionUtilsMixin):
    def __init__(self) -> None:
        self.target_user_ids = ["configured-target"]
        self.data = {
            "users": {
                "role-owner": {"relationship_role": "owner"},
                "role-friend": {"relationship_role": "friend"},
                "canonical-owner": {"relationship_role": "owner"},
            }
        }

    @staticmethod
    def _normalize_private_identity_id(value) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_private_user_role(value) -> str:
        role = str(value or "").strip().lower()
        return role if role in {"owner", "friend"} else ""

    def _configured_target_ids(self) -> list[str]:
        return list(self.target_user_ids)

    @staticmethod
    def _configured_admin_ids() -> set[str]:
        return {"astrbot-admin"}


class InteractionPermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plugin = _PermissionHarness()

    def test_relationship_owner_is_plugin_manager(self) -> None:
        self.assertEqual({"role-owner", "canonical-owner"}, self.plugin._relationship_owner_user_ids())
        self.assertTrue(self.plugin._is_private_companion_owner_user_id("role-owner"))
        self.assertTrue(self.plugin._is_plugin_manager_user_id("role-owner"))

    def test_existing_manager_sources_remain_allowed(self) -> None:
        self.assertTrue(self.plugin._is_plugin_manager_user_id("configured-target"))
        self.assertTrue(self.plugin._is_plugin_manager_user_id("astrbot-admin"))

    def test_friend_role_is_not_plugin_manager(self) -> None:
        self.assertFalse(self.plugin._is_private_companion_owner_user_id("role-friend"))
        self.assertFalse(self.plugin._is_plugin_manager_user_id("role-friend"))

    def test_alias_identity_does_not_inherit_owner_permission(self) -> None:
        self.assertFalse(self.plugin._is_private_companion_owner_user_id("owner-alias"))
        self.assertFalse(self.plugin._is_plugin_manager_user_id("owner-alias"))

    def test_sensitive_location_only_allows_owner_in_private_chat(self) -> None:
        self.assertTrue(self.plugin._can_manage_sensitive_location(_Event("configured-target")))
        self.assertTrue(self.plugin._can_manage_sensitive_location(_Event("role-owner")))
        self.assertFalse(self.plugin._can_manage_sensitive_location(_Event("configured-target", private=False)))
        self.assertFalse(self.plugin._can_manage_sensitive_location(_Event("astrbot-admin")))
        self.assertFalse(self.plugin._can_manage_sensitive_location(_Event("role-friend")))
        self.assertFalse(self.plugin._can_manage_sensitive_location(_Event("owner-alias")))

    def test_sensitive_location_denial_does_not_disclose_configuration(self) -> None:
        denial = self.plugin._sensitive_location_denied_text()
        self.assertNotIn("当前", denial)
        self.assertNotIn("绑定城市", denial)
        self.assertNotIn("LocationID", denial)


if __name__ == "__main__":
    unittest.main()
