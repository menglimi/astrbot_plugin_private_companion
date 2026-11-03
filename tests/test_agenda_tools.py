from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import json
import unittest

from companion_v2.agenda.models import AgendaError
from companion_v2.agenda.service import AgendaService
from companion_v2.conversation.agenda_tools import AgendaRoleSession, TOOL_FIELDS
from companion_v2.conversation.foreground import (
    ClaimKind, DraftClaim, EvidencePromptCompiler, ForegroundContextAssembler, ModelDraft, PromptMaterial,
)
from companion_v2.conversation.models import InboundConversationEvent
from companion_v2.foundation.clock import DeterministicClock
from companion_v2.foundation.refs import AudienceRef, ScopeKind, ScopeRef, SourceRef, SubjectKind, SubjectRef, VersionRef
from companion_v2.identity_policy.models import AuthorizationTier
from companion_v2.sources.models import SourceRecord
from companion_v2.sources.repositories import SourceRepository
from tests import test_agenda as agenda_fixture
from tests.test_agenda import INSTALLATION, NOW, PRINCIPAL, ROLE, SCOPE, TARGET, USER


class RecordingRoleModel:
    def __init__(self, during=None):
        self.requests = []
        self.during = during

    def generate(self, request):
        self.requests.append(request)
        if self.during:
            self.during()
        return ModelDraft("记下来了～", (DraftClaim("role-expression", ClaimKind.SUBJECTIVE, "自然续答"),))


class AgendaToolTests(unittest.TestCase):
    setUp = agenda_fixture.AgendaTests.setUp
    open_runtime = agenda_fixture.AgendaTests.open_runtime
    op = agenda_fixture.AgendaTests.op
    create = agenda_fixture.AgendaTests.create
    change = agenda_fixture.AgendaTests.change

    def session(self, *, group=False, principal=PRINCIPAL, actor=None, event_id="event-a"):
        actor = actor or self.actor
        scope = SCOPE if not group else ScopeRef(INSTALLATION, "onebot", ScopeKind.GROUP, "group-a")
        if group and self.identity.role_binding(scope) is None:
            self.identity.bind_role(scope=scope, role=ROLE, expected_revision=0)
        grant_id = "conversation-" + event_id
        self.identity.issue_grant(grant_id=grant_id, subject=actor.subject, scope=scope, capability="conversation.read",
                                  tier=AuthorizationTier.STANDARD, valid_from=NOW, expected_revision=self.identity.revision)
        reference = SourceRef("source-" + event_id, "conversation", scope, VersionRef("source-" + event_id, 1), NOW)
        audience = AudienceRef(scope, (actor.subject,) if not group else (actor.subject, SubjectRef(INSTALLATION, SubjectKind.HUMAN, "bystander")))
        record = SourceRecord(reference=reference, audience=audience, content_hash="synthetic-hash", payload="帮我记件事")
        sources = SourceRepository(self.runtime)
        sources.publish(record, observed_at=NOW).unwrap()
        event = InboundConversationEvent(event_id, principal, scope, reference, "conversation.read", event_id + "-hash", NOW)
        context = ForegroundContextAssembler().seal(primary=record)
        request = EvidencePromptCompiler().compile(event=event, role_instruction="你是原来的角色，沿用此前语气。",
                                                  role_binding=self.identity.role_binding(scope), context=context)
        clock = DeterministicClock.at(NOW)
        session = AgendaRoleSession(self.runtime, request=request, target=TARGET, audience=audience, now=clock.now, sources=sources)
        return session, clock

    def test_new_tool_session_cannot_stamp_an_old_prompt_with_a_new_role_binding(self):
        original, clock = self.session()
        self.identity.bind_role(scope=SCOPE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b"), expected_revision=1)
        with self.assertRaises(AgendaError) as failure:
            AgendaRoleSession(self.runtime, request=original.request, target=TARGET, audience=original.audience,
                              now=clock.now, sources=original.sources)
        self.assertEqual(failure.exception.code, "role_binding_changed")
        self.assertEqual(self.runtime.read_all("SELECT * FROM agenda_memos", ()), ())

    def test_group_continuation_rechecks_audience_for_original_context_material(self):
        private, _ = self.session(event_id="private-context")
        group, _ = self.session(group=True, event_id="group-context")
        group.execute(call_id="read", action="memo.list", arguments={}).unwrap()
        group.request = replace(group.request, context=replace(group.request.context, materials=(
            *group.request.context.materials, PromptMaterial("memory", "private", "只给本人的资料", private.request.event.source),
        )))
        model = RecordingRoleModel()
        self.assertEqual(group.continue_role(model).failure.code, "tool_material_audience_mismatch")
        self.assertEqual(model.requests, [])

    def test_original_role_continues_once_from_actual_save_without_implicit_reminder(self):
        session, _ = self.session()
        result = session.execute(call_id="save", action="memo.create", arguments={"title": "日常安排", "body": "先记下来"}).unwrap()
        payload = json.loads(result.payload_json)
        self.assertEqual(payload["scheduling"], "not_requested")
        self.assertEqual(self.runtime.read_all("SELECT * FROM agenda_reminders", ()), ())
        model = RecordingRoleModel()
        session.continue_role(model).unwrap()
        self.assertEqual(len(model.requests), 1)
        request = model.requests[0]
        self.assertEqual(request.role_instruction, session.request.role_instruction)
        self.assertEqual(request.event, session.request.event)
        self.assertEqual(request.context.materials[:-1], session.request.context.materials)
        self.assertIn("actual_operation_results", request.context.materials[-1].purpose)
        self.assertIn("local_registered/local_prepared", request.instruction)
        self.assertEqual(self.runtime.read_all("SELECT * FROM outbox", ()), ())

    def test_delete_is_exposed_only_through_the_memo_delete_tool(self):
        session, _ = self.session()
        created = session.execute(call_id="save", action="memo.create",
                                  arguments={"title": "午饭的事", "body": "改天再聊"}).unwrap()
        payload = json.loads(created.payload_json)
        arguments = {"memo_id": payload["object_id"], "expected_revision": payload["revision"]}
        deleted = session.execute(call_id="remove", action="memo.delete", arguments=arguments).unwrap()
        result = json.loads(deleted.payload_json)
        self.assertEqual(result["state"], "deleted")
        self.assertEqual(result["object_id"], payload["object_id"])
        stale = session.execute(call_id="remove-again", action="memo.delete", arguments=arguments).unwrap()
        self.assertEqual(stale.state, "failed")
        self.assertEqual(session.execute(call_id="legacy", action="memo.delete.commit",
                                         arguments={}).failure.code, "tool_not_available")

    def test_model_cannot_replace_owner_role_scope_identity_or_invoke_internal_due(self):
        session, _ = self.session()
        for injected in ("actor", "owner", "scope", "role", "expected_role_revision", "recipient"):
            with self.subTest(injected=injected):
                result = session.execute(call_id=injected, action="memo.create", arguments={"title": "x", "body": "", injected: "admin"})
                self.assertEqual(result.failure.code, "invalid_tool_arguments")
        self.assertEqual(session.execute(call_id="due", action="reminder.deliver", arguments={}).failure.code, "tool_not_available")
        self.assertEqual(session.execute(call_id="delete", action="memo.delete.commit", arguments={}).failure.code, "tool_not_available")
        self.assertEqual(self.runtime.read_all("SELECT * FROM agenda_memos", ()), ())

    def test_same_tool_call_replays_and_changed_arguments_conflict(self):
        session, _ = self.session()
        arguments = {"title": "one", "body": ""}
        first = session.execute(call_id="save", action="memo.create", arguments=arguments).unwrap()
        self.assertEqual(session.execute(call_id="save", action="memo.create", arguments=arguments).unwrap(), first)
        self.assertEqual(session.execute(call_id="save", action="memo.create", arguments={"title": "two", "body": ""}).failure.code,
                         "tool_call_conflict")
        replacement_session = AgendaRoleSession(self.runtime, request=session.request, target=TARGET, audience=session.audience,
                                                now=session.now, sources=session.sources)
        receipt = replacement_session.execute(call_id="save", action="memo.create", arguments=arguments).unwrap()
        self.assertTrue(json.loads(receipt.payload_json)["replayed"])
        self.assertEqual(len(self.runtime.read_all("SELECT * FROM agenda_memos", ())), 1)

    def test_failed_operation_is_tool_result_and_normal_role_continuation_remains_available(self):
        session, _ = self.session()
        result = session.execute(call_id="read", action="memo.read", arguments={"memo_id": "missing"}).unwrap()
        self.assertEqual(result.state, "failed")
        self.assertEqual(json.loads(result.payload_json)["code"], "agenda_object_not_found")
        model = RecordingRoleModel()
        self.assertTrue(session.continue_role(model).is_success)
        self.assertIn('"state":"failed"', model.requests[0].context.materials[-1].payload)

    def test_same_turn_create_then_edit_keeps_original_receipt_without_blocking_continuation(self):
        session, _ = self.session()
        first = session.execute(call_id="save", action="memo.create", arguments={"title": "草稿", "body": ""}).unwrap()
        original = json.loads(first.payload_json)
        session.execute(call_id="edit", action="memo.edit", arguments={"memo_id": original["object_id"], "expected_revision": 1,
                                                                       "changes": {"title": "修订标题"}}).unwrap()
        model = RecordingRoleModel()
        session.continue_role(model).unwrap()
        results = json.loads(model.requests[0].context.materials[-1].payload)
        self.assertEqual([json.loads(result["payload_json"])["revision"] for result in results], [1, 2])

    def test_cancellation_and_role_binding_change_stop_new_mutation(self):
        session, _ = self.session()
        session.cancel()
        self.assertEqual(session.execute(call_id="save", action="memo.create", arguments={"title": "x", "body": ""}).failure.code,
                         "role_turn_cancelled")
        other, _ = self.session(event_id="event-b")
        self.identity.bind_role(scope=SCOPE, role=SubjectRef(INSTALLATION, SubjectKind.BOT, "role-b"), expected_revision=1)
        self.assertEqual(other.execute(call_id="save", action="memo.create", arguments={"title": "x", "body": ""}).failure.code,
                         "role_binding_changed")
        self.assertEqual(self.runtime.read_all("SELECT * FROM agenda_memos", ()), ())

    def test_group_management_executes_but_private_body_never_reaches_role_model(self):
        self.create(title="私密标题", body="不可发给旁观者的正文").unwrap()
        self.service.policy.set_tier(USER, AuthorizationTier.CORE, expected_revision=self.service.policy.revision, observed_at=NOW)
        session, _ = self.session(group=True)
        result = session.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        self.assertEqual(result.state, "private_result_required")
        self.assertNotIn("私密标题", result.payload_json)
        model = RecordingRoleModel()
        session.continue_role(model).unwrap()
        self.assertNotIn("不可发给旁观者", model.requests[0].context.materials[-1].payload)
        self.assertNotIn("私密标题", model.requests[0].context.materials[-1].payload)
        self.assertEqual(self.runtime.read_all("SELECT * FROM outbox", ()), ())

    def test_object_revision_or_read_revocation_invalidates_material_before_generation(self):
        self.create().unwrap()
        session, _ = self.session()
        session.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        self.change("edit", changes={"body": "new"}).unwrap()
        model = RecordingRoleModel()
        self.assertEqual(session.continue_role(model).failure.code, "tool_result_changed")
        self.assertEqual(model.requests, [])
        other, _ = self.session(event_id="event-b")
        other.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        self.identity.revoke_grant("memo.read", revoked_at=NOW, expected_revision=self.identity.revision)
        self.assertFalse(other.continue_role(model).is_success)
        self.assertEqual(model.requests, [])

    def test_revocation_during_generation_discards_draft_and_does_not_send(self):
        self.create().unwrap()
        session, _ = self.session()
        session.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        model = RecordingRoleModel(lambda: self.identity.revoke_grant("memo.read", revoked_at=NOW, expected_revision=self.identity.revision))
        self.assertEqual(session.continue_role(model).failure.code, "authorization_revoked")
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(self.runtime.read_all("SELECT * FROM outbox", ()), ())

    def test_audience_change_during_generation_discards_private_draft(self):
        self.create().unwrap()
        session, _ = self.session()
        session.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        def change_audience():
            session.audience = AudienceRef(SCOPE, (USER, SubjectRef(INSTALLATION, SubjectKind.HUMAN, "new-person")))
        model = RecordingRoleModel(change_audience)
        self.assertEqual(session.continue_role(model).failure.code, "tool_audience_changed")

    def test_current_clock_rechecks_expiring_shared_material(self):
        self.create().unwrap()
        principal = replace(PRINCIPAL, native_account_id="reader")
        reader_subject = SubjectRef(INSTALLATION, SubjectKind.HUMAN, "reader")
        self.identity.bind(binding_id="reader", principal=principal, subject=reader_subject, valid_from=NOW, expected_revision=self.identity.revision)
        self.service.policy.set_tier(reader_subject, AuthorizationTier.IMPORTANT, expected_revision=self.service.policy.revision, observed_at=NOW)
        reader = self.identity.resolve_authenticated(principal, observed_at=NOW).unwrap()
        self.service.policy.share_object(self.service.object_key("memo", "memo-a"), reader_subject, ("memo.read",),
                                         expected_revision=self.service.policy.revision, observed_at=NOW, expires_at=NOW + timedelta(seconds=1))
        session, clock = self.session(principal=principal, actor=reader)
        session.execute(call_id="read", action="memo.read", arguments={"memo_id": "memo-a"}).unwrap()
        clock.advance(timedelta(seconds=1))
        self.assertFalse(session.continue_role(RecordingRoleModel()).is_success)

    def test_explicit_reminder_only_reports_local_registration_and_preserves_target(self):
        session, _ = self.session()
        result = session.execute(call_id="remind", action="reminder.create", arguments={
            "schedule": {"anchor": NOW.isoformat(), "timezone_name": "Asia/Shanghai"}, "message": "喝水",
        }).unwrap()
        receipt = json.loads(result.payload_json)
        self.assertEqual((receipt["scheduling"], receipt["delivery"]), ("local_registered", "not_started"))
        self.assertEqual(self.service.repository.reminder(receipt["object_id"]).target.owner, USER)
        self.assertEqual(self.runtime.read_all("SELECT * FROM agenda_memos", ()), ())


if __name__ == "__main__":
    unittest.main()

class AgendaToolSchemaTests(unittest.TestCase):
    def test_reminder_create_schema_declares_absolute_schedule_and_message(self):
        from companion_v2.conversation.tool_loop import specification

        schema = json.loads(specification("reminder.create", *TOOL_FIELDS["reminder.create"]).parameters_json)
        schedule = schema["properties"]["schedule"]

        self.assertEqual(schedule["type"], "object")
        self.assertEqual(set(schedule["required"]), {"anchor", "timezone_name"})
        self.assertEqual(schedule["properties"]["anchor"]["type"], "string")
        self.assertIn("ISO-8601", schedule["properties"]["anchor"]["description"])
        self.assertEqual(schedule["properties"]["timezone_name"]["type"], "string")
        self.assertEqual(schema["properties"]["message"]["type"], "string")
        self.assertFalse(schema["additionalProperties"])
        # 直接给时间，或关联已有便签；两种合法用法都不能被 required 拦死。
        self.assertEqual(len(schema["anyOf"]), 2)
        self.assertEqual(set(schema["anyOf"][0]["required"]), {"schedule", "message"})
        self.assertEqual(set(schema["anyOf"][1]["required"]), {"memo_id", "expected_memo_revision"})

    def test_schedule_fold_stays_a_bounded_integer_instead_of_an_enum(self):
        from companion_v2.conversation.tool_loop import specification

        schedule = json.loads(specification(
            "reminder.create", *TOOL_FIELDS["reminder.create"]).parameters_json)["properties"]["schedule"]

        fold = schedule["properties"]["fold"]
        # 整数 enum 会让 Gemini 在本地构造请求时失败；边界与说明承担同样的约束。
        self.assertEqual(fold["type"], "integer")
        self.assertNotIn("enum", fold)
        self.assertEqual((fold["minimum"], fold["maximum"]), (0, 1))
        self.assertIn("0", fold["description"])
        self.assertIn("1", fold["description"])
        # 字符串 enum 本来就是每个 provider 都能构造的，修复不能把它们一起删掉。
        self.assertEqual(schedule["properties"]["frequency"]["enum"],
                         ["once", "daily", "weekly", "monthly", "yearly"])
        self.assertEqual(schedule["properties"]["gap_policy"]["enum"], ["reject", "shift_forward"])

    def test_reminder_instruction_forbids_memo_substitution(self):
        from companion_v2.conversation.tool_loop import TOOL_INSTRUCTION

        self.assertIn("reminder.create", TOOL_INSTRUCTION)
        self.assertIn("memo.create", TOOL_INSTRUCTION)
