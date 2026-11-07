from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import unittest

from companion_v2.agenda.models import Schedule
from companion_v2.agenda.service import AgendaService
from companion_v2.foundation.refs import ScopeKind, ScopeRef, SubjectKind, SubjectRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.identity_policy.personas import ConversationRoute, PersonaDirectory
from tests import test_agenda as agenda_fixture
from tests.test_agenda import INSTALLATION, NOW, PRINCIPAL, ROLE, SCOPE, TARGET, USER


class AgendaAccessTests(unittest.TestCase):
    setUp = agenda_fixture.AgendaTests.setUp
    open_runtime = agenda_fixture.AgendaTests.open_runtime
    op = agenda_fixture.AgendaTests.op
    create = agenda_fixture.AgendaTests.create
    change = agenda_fixture.AgendaTests.change
    memo = agenda_fixture.AgendaTests.memo
    remind = agenda_fixture.AgendaTests.remind
    due_claim = agenda_fixture.AgendaTests.due_claim

    def person(self, name, tier):
        subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, name)
        principal = replace(PRINCIPAL, native_account_id=name)
        self.identity.bind(binding_id="binding-" + name, principal=principal, subject=subject,
                           valid_from=NOW, expected_revision=self.identity.revision)
        self.service.policy.set_tier(subject, tier, expected_revision=self.service.policy.revision, observed_at=NOW)
        return self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()

    def share(self, actor, kind, identifier, *actions, expires_at=None):
        self.service.policy.share_object(self.service.object_key(kind, identifier), actor.subject, tuple(actions),
                                         expected_revision=self.service.policy.revision, observed_at=NOW, expires_at=expires_at)

    def test_core_creates_for_another_person_without_rewriting_creator_or_recipient(self):
        admin = self.person("admin", AuthorizationTier.CORE)
        receipt = self.service.create_memo(admin, target=TARGET, memo_id="delegated", title="A 的事项", body="由管理员代记",
                                           operation=self.op(), expected_role_revision=1, observed_at=NOW).unwrap()
        memo = self.service.read_memo(admin, target=TARGET, memo_id="delegated", observed_at=NOW).unwrap()
        self.assertEqual(memo.creator.subject, admin.subject)
        self.assertEqual(memo.target.owner, USER)
        operation = self.op()
        self.service.create_reminder(admin, target=TARGET, reminder_id="delegated-reminder", message="A 的提醒",
                                     schedule=Schedule(NOW, "Asia/Shanghai"), operation=operation,
                                     expected_role_revision=1, observed_at=NOW).unwrap()
        prepared = self.service.prepare_local(self.due_claim(), observed_at=NOW).unwrap()
        self.assertEqual(prepared.recipient, USER)
        self.assertEqual(self.service.repository.reminder("delegated-reminder").requester.subject, admin.subject)
        self.assertTrue(self.service.receipt(admin, target=TARGET, operation_id=receipt.operation_id, observed_at=NOW).is_success)
        self.service.policy.set_tier(admin.subject, AuthorizationTier.STANDARD,
                                     expected_revision=self.service.policy.revision, observed_at=NOW)
        self.assertFalse(self.service.validate_preparation(prepared.occurrence_id, observed_at=NOW).is_success)
        self.assertFalse(self.service.create_reminder(admin, target=TARGET, reminder_id="delegated-reminder", message="A 的提醒",
                                                     schedule=Schedule(NOW, "Asia/Shanghai"), operation=operation,
                                                     expected_role_revision=1, observed_at=NOW).is_success)

    def test_core_can_manage_other_roles_after_window_rebind_but_due_remains_bound(self):
        admin = self.person("admin", AuthorizationTier.CORE)
        self.create().unwrap()
        other_role = SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b")
        self.identity.bind_role(scope=SCOPE, role=other_role, expected_revision=1)
        memo = self.service.read_memo(admin, target=TARGET, memo_id="memo-a", observed_at=NOW).unwrap()
        self.service.change_memo(admin, target=TARGET, memo_id="memo-a", action="edit", changes={"body": "跨角色管理"},
                                 expected_revision=memo.revision, expected_role_revision=2, operation=self.op(), observed_at=NOW).unwrap()
        self.assertEqual(self.service.repository.memo("memo-a").target.role, ROLE)
        self.assertFalse(self.service.read_memo(self.actor, target=TARGET, memo_id="memo-a", observed_at=NOW).is_success)

    def test_important_can_use_own_records_without_per_action_grants(self):
        important = self.person("important", AuthorizationTier.IMPORTANT)
        target = replace(TARGET, owner=important.subject)
        self.service.create_memo(important, target=target, memo_id="own", title="自己的", body="", operation=self.op(),
                                 expected_role_revision=1, observed_at=NOW).unwrap()
        self.assertEqual(len(self.service.list_memos(important, target=target, observed_at=NOW).unwrap()), 1)
        self.assertFalse(self.service.create_memo(important, target=TARGET, memo_id="other", title="其他人的", body="",
                                                 operation=self.op(), expected_role_revision=1, observed_at=NOW).is_success)

    def test_read_share_filters_before_search_sort_and_page_without_edit_or_other_items(self):
        hidden_operation = self.create(memo_id="a", body="hidden keyword").unwrap()
        self.create(memo_id="z", body="shared").unwrap()
        reader = self.person("reader", AuthorizationTier.IMPORTANT)
        self.share(reader, "memo", "z", "memo.read")
        listed = self.service.list_memos(reader, target=TARGET, observed_at=NOW, limit=1).unwrap()
        self.assertEqual(tuple(m.memo_id for m in listed), ("z",))
        self.assertEqual(self.service.list_memos(reader, target=TARGET, observed_at=NOW, search="hidden").unwrap(), ())
        result = self.service.change_memo(reader, target=TARGET, memo_id="z", action="edit", changes={"body": "unauthorized"},
                                          expected_revision=1, expected_role_revision=1, operation=self.op(), observed_at=NOW)
        self.assertEqual(result.failure.code, "agenda_object_not_found")
        failures = [self.service.read_memo(reader, target=TARGET, memo_id=identifier, observed_at=NOW).failure.code
                    for identifier in ("a", "missing")]
        self.assertEqual(failures, ["agenda_object_not_found"] * 2)
        receipt_failures = [self.service.receipt(reader, target=TARGET, operation_id=identifier, observed_at=NOW).failure
                            for identifier in (hidden_operation.operation_id, "missing-operation")]
        self.assertEqual([failure.code for failure in receipt_failures], ["agenda_object_not_found"] * 2)
        self.assertEqual(receipt_failures[0].reason, receipt_failures[1].reason)

    def test_standard_needs_both_feature_opening_and_object_share(self):
        self.create().unwrap()
        reader = self.person("standard", AuthorizationTier.STANDARD)
        self.share(reader, "memo", "memo-a", "memo.read")
        self.assertFalse(self.service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW).is_success)
        for action in ("memo.read", "memo.list"):
            self.identity.issue_grant(grant_id="standard-" + action, subject=reader.subject, scope=SCOPE, capability=action,
                                      tier=AuthorizationTier.CORE, valid_from=NOW, expected_revision=self.identity.revision)
        self.assertTrue(self.service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW).is_success)
        self.assertEqual(len(self.service.list_memos(reader, target=TARGET, observed_at=NOW).unwrap()), 1)
        self.create(memo_id="not-shared").unwrap()
        self.assertFalse(self.service.read_memo(reader, target=TARGET, memo_id="not-shared", observed_at=NOW).is_success)

    def test_verified_private_owner_reads_own_memos_without_a_window_grant(self):
        scope = ScopeRef(INSTALLATION, "onebot", ScopeKind.PRIVATE, "person-a", "bot-a", "account-a",
                         "conversation-a", "session-a")
        target = replace(TARGET, scope=scope)
        PersonaDirectory(self.runtime).register_route(ConversationRoute(scope, user=USER), observed_at=NOW)
        self.identity.bind_role(scope=scope, role=ROLE, expected_revision=0)
        admin = self.person("admin", AuthorizationTier.CORE)
        operation = self.op("private-owner-memo")
        operation = replace(operation, scope=scope)
        self.service.create_memo(admin, target=target, memo_id="owner-only", title="私聊事项", body="仅本人可读",
                                 operation=operation, expected_role_revision=1, observed_at=NOW).unwrap()
        self.assertFalse(self.identity.authorize(self.actor, scope=scope, capability="memo.read",
                                                 observed_at=NOW).is_success)
        self.assertEqual(self.service.read_memo(self.actor, target=target, memo_id="owner-only",
                                                observed_at=NOW).unwrap().body, "仅本人可读")
        self.assertEqual(tuple(memo.memo_id for memo in self.service.list_memos(
            self.actor, target=target, observed_at=NOW).unwrap()), ("owner-only",))
        self.assertFalse(self.service.change_memo(self.actor, target=target, memo_id="owner-only", action="edit",
            changes={"body": "未授权修改"}, expected_revision=1, expected_role_revision=1,
            operation=replace(self.op("private-owner-edit"), scope=scope), observed_at=NOW).is_success)
        other = self.person("other", AuthorizationTier.STANDARD)
        self.assertFalse(self.service.read_memo(other, target=target, memo_id="owner-only", observed_at=NOW).is_success)
        self.assertFalse(self.service.list_memos(other, target=target, observed_at=NOW).is_success)

    def test_expiring_share_and_other_connection_revocation_are_seen_without_reload(self):
        self.create().unwrap()
        reader = self.person("reader", AuthorizationTier.IMPORTANT)
        self.share(reader, "memo", "memo-a", "memo.read", expires_at=NOW + timedelta(seconds=1))
        self.assertTrue(self.service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW).is_success)
        self.assertFalse(self.service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW + timedelta(seconds=1)).is_success)
        other = AgendaService(self.open_runtime())
        other.policy.revoke_share(other.object_key("memo", "memo-a"), reader.subject,
                                  expected_revision=other.policy.revision, observed_at=NOW)
        self.assertFalse(self.service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW).is_success)
        self.assertEqual(self.service.list_memos(reader, target=TARGET, observed_at=NOW).unwrap(), ())

    def test_shared_editor_changes_content_without_becoming_its_author(self):
        self.create().unwrap()
        editor = self.person("editor", AuthorizationTier.IMPORTANT)
        self.share(editor, "memo", "memo-a", "memo.edit")
        operation = self.op()
        self.service.change_memo(editor, target=TARGET, memo_id="memo-a", action="edit", changes={"body": "共享修订"},
                                 expected_revision=1, expected_role_revision=1, operation=operation, observed_at=NOW).unwrap()
        memo = self.memo()
        self.assertEqual(memo.creator.subject, USER)
        self.assertEqual(memo.target.owner, USER)
        self.assertEqual(memo.body, "共享修订")
        self.assertEqual(self.runtime.read_one("SELECT subject_id FROM agenda_results WHERE operation_id = ?",
                                               (operation.operation_id,))["subject_id"], editor.subject.subject_id)
        self.assertTrue(self.service.receipt(editor, target=TARGET, operation_id=operation.operation_id, observed_at=NOW).is_success)

    def test_reopen_retains_shares_and_registration_preserves_original_owner(self):
        self.create().unwrap()
        reader = self.person("reader", AuthorizationTier.IMPORTANT)
        self.share(reader, "memo", "memo-a", "memo.read")
        self.runtime.close()
        runtime = self.open_runtime()
        service = AgendaService(runtime)
        self.assertEqual(service.read_memo(reader, target=TARGET, memo_id="memo-a", observed_at=NOW).unwrap().target.owner, USER)
        revision = service.policy.revision
        AgendaService(runtime)
        self.assertEqual(service.policy.revision, revision)

    def test_group_scope_does_not_reduce_core_privilege_or_grant_others_access(self):
        admin = self.person("admin", AuthorizationTier.CORE)
        group = ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "group-a")
        self.identity.bind_role(scope=group, role=ROLE, expected_revision=0)
        target = replace(TARGET, scope=group)
        self.service.create_memo(admin, target=target, memo_id="group-managed", title="私密资料", body="不能自动群发",
                                 operation=replace(self.op(), scope=group), expected_role_revision=1, observed_at=NOW).unwrap()
        self.assertTrue(self.service.read_memo(admin, target=target, memo_id="group-managed", observed_at=NOW).is_success)
        stranger = self.person("group-member", AuthorizationTier.IMPORTANT)
        self.assertFalse(self.service.read_memo(stranger, target=target, memo_id="group-managed", observed_at=NOW).is_success)


if __name__ == "__main__":
    unittest.main()
