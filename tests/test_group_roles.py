# -*- coding: utf-8 -*-
"""Group standing: host parsing plus prompt material, no authorization effect."""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace as NS

from astrbot_host.group_standing import group_sender_standing
from companion_v2.conversation.group_roles import GroupRoleStore
from companion_v2.foundation.refs import ScopeKind
from tests.g6_support import G6Fixture, NOW


def event_with_sender(role, user_id=1830924012, group_id=1097283005):
    return NS(message_obj=NS(raw_message={"group_id": group_id,
                                          "sender": {"role": role, "user_id": user_id,
                                                     "nickname": "星缘", "card": "管理员 星缘"}}))


class GroupStandingParsingTests(G6Fixture):
    def test_onebot_sender_roles_are_read(self):
        self.assertEqual(group_sender_standing(event_with_sender("owner")),
                         {"account_id": "1830924012", "role": "owner", "group_id": "1097283005"})
        self.assertEqual(group_sender_standing(event_with_sender("admin"))["role"], "admin")
        self.assertEqual(group_sender_standing(event_with_sender("member"))["role"], "member")

    def test_payloads_without_a_trustworthy_role_are_ignored(self):
        self.assertIsNone(group_sender_standing(NS(message_obj=NS(raw_message=None))))
        self.assertIsNone(group_sender_standing(NS(message_obj=NS(raw_message={"sender": {}}))))
        self.assertIsNone(group_sender_standing(event_with_sender("stranger")))
        self.assertIsNone(group_sender_standing(event_with_sender("owner", user_id="openid-abc")))


class GroupStandingPromptTests(G6Fixture):
    def setUp(self):
        super().setUp()
        scope = replace(self.scope, kind=ScopeKind.GROUP, native_scope_id="1097283005",
                        bot_id="bot-a", account_id="100",
                        session_id="1097283005", conversation_id="group-conversation")
        self.group = NS(scope=scope, actor=NS(subject=self.subject,
                                              principal=NS(native_account_id="1830924012")),
                        role_binding=NS(role=self.role))
        self.store = GroupRoleStore(self.runtime)

    def remember(self, role, account="1830924012", minutes=0):
        with self.runtime.transaction() as connection:
            self.store.remember(connection, self.group, account_id=account, role=role,
                                at=NOW + timedelta(minutes=minutes))

    def test_owner_speaker_is_described_as_the_group_owner(self):
        self.remember("owner")
        described = self.store.describe(self.group)
        self.assertEqual(described["group_owner_id"], "1830924012")
        self.assertEqual(described["speaker_role"], "owner")
        self.assertEqual(described["speaker_role_label"], "群主")
        self.assertTrue(described["speaker_is_group_owner"])

    def test_admin_speaker_keeps_the_owner_id_separate(self):
        self.remember("owner", account="10001")
        self.remember("admin")
        described = self.store.describe(self.group)
        self.assertEqual(described["group_owner_id"], "10001")
        self.assertEqual(described["speaker_role_label"], "管理员")
        self.assertFalse(described["speaker_is_group_owner"])

    def test_unknown_speaker_has_no_invented_standing(self):
        described = self.store.describe(self.group)
        self.assertEqual(described, {"group_owner_id": ""})

    def test_owner_change_does_not_leave_two_owners(self):
        self.remember("owner", account="10001")
        self.remember("owner", account="10002", minutes=1)
        document = self.store.read(
            __import__("companion_v2.memory.group.access", fromlist=["owner_key"]).owner_key(self.group))
        self.assertEqual(document["owner_id"], "10002")
        self.assertEqual([account for account, record in document["members"].items()
                          if record["role"] == "owner"], ["10002"])

    def test_member_count_stays_bounded(self):
        for index in range(80):
            self.remember("member", account=str(20000 + index), minutes=index)
        document = self.store.read(
            __import__("companion_v2.memory.group.access", fromlist=["owner_key"]).owner_key(self.group))
        self.assertLessEqual(len(document["members"]), 64)

    def test_speaker_contribution_carries_the_standing(self):
        import json

        from companion_v2.conversation.group_context import GroupConversationContext

        self.remember("owner")
        context = GroupConversationContext(NS(runtime=self.runtime, tasks=None))
        contribution = context.speaker(self.group, [NS(source=NS(source_id="source-1"))])
        payload = json.loads(contribution.payload)
        self.assertEqual(payload["speaker_id"], "1830924012")
        self.assertEqual(payload["group_owner_id"], "1830924012")
        self.assertEqual(payload["speaker_role"], "owner")
        self.assertEqual(payload["speaker_role_label"], "群主")
        self.assertTrue(payload["speaker_is_group_owner"])

    def test_speaker_contribution_stays_minimal_without_standing(self):
        import json

        from companion_v2.conversation.group_context import GroupConversationContext

        context = GroupConversationContext(NS(runtime=self.runtime, tasks=None))
        payload = json.loads(context.speaker(self.group, []).payload)
        for field in ("speaker_role", "speaker_role_label", "speaker_is_group_owner", "group_owner_id"):
            self.assertNotIn(field, payload)
